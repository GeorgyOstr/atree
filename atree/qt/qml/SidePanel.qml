import QtQuick

// Frame and folding title bar shared by the side panels.
Rectangle {
    id: panel
    property string title: ""
    property string note: ""
    property string action: ""
    property bool folded: false
    property real bodyHeight: 0
    property int fadeDuration: Theme.slow
    default property alias body: content.data
    signal actionClicked()
    signal toggled()

    height: folded ? 32 : 32 + bodyHeight
    Behavior on height { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    color: Theme.panel
    border.color: Theme.line
    radius: 10
    clip: true

    MouseArea {
        // presses on the panel never reach the canvas
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        onPressed: Ui.markTap()
    }

    Item {
        id: bar
        objectName: "panelTitle"
        width: parent.width
        height: 32
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: { Ui.markTap(); panel.toggled() }
        }
        Row {
            anchors.left: parent.left
            anchors.leftMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            spacing: 7
            Chevron {
                open: !panel.folded
                font.pixelSize: 16
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: panel.title
                font.pixelSize: 13
                font.bold: true
                color: Theme.text
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: panel.note
                textFormat: Text.PlainText
                font.pixelSize: 11
                color: Theme.dim
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        Text {
            id: actionText
            visible: panel.action !== "" && !panel.folded
            anchors.right: parent.right
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            text: panel.action
            font.pixelSize: 11
            color: actionArea.containsMouse ? Theme.text : Theme.muted
            MouseArea {
                id: actionArea
                anchors.fill: parent
                anchors.margins: -4
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: { Ui.markTap(); panel.actionClicked() }
            }
        }
    }

    Item {
        id: content
        y: 32
        width: parent.width
        height: Math.max(0, panel.height - 32)
        opacity: panel.folded ? 0 : 1
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: panel.fadeDuration } }
    }
}
