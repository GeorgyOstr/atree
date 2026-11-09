import QtQuick

Rectangle {
    id: panel
    objectName: "aiPanel"
    readonly property var ai: backend.ai
    readonly property var results: ai.results || []
    signal cleared()

    visible: results.length > 0 || (ai.error || "") !== ""
    height: visible ? body.implicitHeight + 16 : 0
    color: Theme.panel
    border.color: Theme.line
    radius: 10
    clip: true

    MouseArea {
        anchors.fill: parent
        onPressed: Ui.markTap()
    }

    Column {
        id: body
        x: 8
        y: 8
        width: parent.width - 16
        spacing: 2
        Item {
            width: parent.width
            height: 22
            Text {
                anchors.left: parent.left
                anchors.right: clearText.left
                anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                elide: Text.ElideRight
                textFormat: Text.PlainText
                font.pixelSize: 12
                font.bold: panel.results.length > 0
                color: panel.results.length > 0 ? Theme.text : Theme.bad
                text: panel.results.length > 0
                      ? "✦ " + panel.ai.agent + " · " + panel.results.length + " fits for “" + panel.ai.query + "”"
                      : (panel.ai.error.indexOf(panel.ai.agent) === 0 ? panel.ai.error
                                                                      : panel.ai.agent + ": " + panel.ai.error)
            }
            Text {
                id: clearText
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "clear ✕"
                font.pixelSize: 11
                color: clearArea.containsMouse ? Theme.text : Theme.dim
                MouseArea {
                    id: clearArea
                    anchors.fill: parent
                    anchors.margins: -3
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { Ui.markTap(); backend.clearAi(); panel.cleared() }
                }
            }
        }
        Repeater {
            model: panel.results
            Rectangle {
                id: res
                required property var modelData
                required property int index
                width: body.width
                height: 40
                radius: 6
                color: resArea.containsMouse ? Theme.panel2 : "transparent"
                Rectangle {
                    x: 6
                    y: 8
                    width: 8
                    height: 8
                    radius: 4
                    color: Theme.toolColor(res.modelData.tool)
                }
                Text {
                    objectName: "aiResultTitle"
                    x: 20
                    y: 3
                    width: parent.width - 26
                    text: (res.index + 1) + ". " + res.modelData.title
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: 12
                    color: Theme.text
                }
                Text {
                    x: 20
                    y: 21
                    width: parent.width - 26
                    text: res.modelData.why + "  ·  " + res.modelData.folder
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: 11
                    color: Theme.muted
                }
                MouseArea {
                    id: resArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { Ui.markTap(); backend.select(res.modelData.key, false) }
                    onContainsMouseChanged: containsMouse
                        ? Ui.showTip(res, res.modelData.title + "\n" + res.modelData.why + "\n" + res.modelData.folder, 600)
                        : Ui.hideTip(res)
                }
            }
        }
    }
}
