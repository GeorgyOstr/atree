import QtQuick

Item {
    id: pill
    objectName: "folderPill"
    property var node
    readonly property string path: node.path
    readonly property bool hovered: area.containsMouse || menuArea.containsMouse || playArea.containsMouse

    x: node.nx
    y: node.ny
    width: Math.max(node.nw, content.implicitWidth + 52)
    height: Theme.pillH
    Behavior on x { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on width { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }

    opacity: 0
    Component.onCompleted: opacity = 1
    Behavior on opacity { NumberAnimation { duration: Theme.slow } }

    Rectangle {
        anchors.fill: parent
        radius: 8
        color: pill.node.drop ? Theme.selection : (pill.hovered ? Theme.panel3 : Theme.panel2)
        border.color: pill.node.drop ? Theme.accent : (pill.hovered ? Theme.dim : Theme.line)
        border.width: pill.node.drop ? 2 : 1
        Behavior on color { ColorAnimation { duration: Theme.quick } }
        Behavior on border.color { ColorAnimation { duration: Theme.quick } }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: Qt.PointingHandCursor
        onClicked: mouse => {
            Ui.markTap()
            Ui.hideTip(pill)
            if (mouse.button === Qt.RightButton)
                Ui.app.openFolderMenu(pill.path, pill, mouse.x, mouse.y)
            else
                backend.toggleDir(pill.path)
        }
        onContainsMouseChanged: {
            if (containsMouse)
                Ui.showTip(pill, backend.tildify(pill.path) + "\n" + Theme.plural(pill.node.count, "talk")
                           + (pill.node.live > 0 ? ", " + pill.node.live + " running" : "")
                           + "\nclick to " + (pill.node.collapsed ? "unfold" : "fold"), 700)
            else
                Ui.hideTip(pill)
        }
    }

    Row {
        id: content
        x: 9
        objectName: "pillContent"
        anchors.verticalCenter: parent.verticalCenter
        spacing: 7
        Chevron {
            open: !pill.node.collapsed
            anchors.verticalCenter: parent.verticalCenter
            anchors.verticalCenterOffset: -1
        }
        Text {
            text: pill.node.label
            textFormat: Text.PlainText
            font.pixelSize: 13
            font.bold: true
            color: Theme.text
            anchors.verticalCenter: parent.verticalCenter
        }
        Text {
            text: pill.node.collapsed && pill.node.age ? pill.node.count + " · " + pill.node.age : String(pill.node.count)
            font.pixelSize: 11
            color: Theme.muted
            anchors.verticalCenter: parent.verticalCenter
        }
    }

    LiveDot {
        visible: pill.node.live > 0 && !pill.hovered
        anchors.right: parent.right
        anchors.rightMargin: 9
        anchors.verticalCenter: parent.verticalCenter
    }
    Text {
        id: playGlyph
        objectName: "pillPlay"
        visible: pill.hovered && backend.talkAgent.name !== undefined
        anchors.right: menuGlyph.left
        anchors.rightMargin: 6
        anchors.verticalCenter: parent.verticalCenter
        text: "▶"
        font.pixelSize: 11
        color: playArea.containsMouse ? Theme.text : Theme.muted
        MouseArea {
            id: playArea
            anchors.fill: parent
            anchors.margins: -3
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onContainsMouseChanged: {
                if (containsMouse) {
                    Ui.hideTip(pill)
                    Ui.showTip(playGlyph, "New " + backend.talkAgent.label + " talk in this folder", 500)
                } else
                    Ui.hideTip(playGlyph)
            }
            onClicked: {
                Ui.markTap()
                Ui.hideTip(playGlyph)
                backend.newTalk(pill.path)
            }
        }
    }
    Text {
        id: menuGlyph
        visible: pill.hovered
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        text: "⋯"
        font.pixelSize: 16
        color: menuArea.containsMouse ? Theme.text : Theme.muted
        MouseArea {
            id: menuArea
            anchors.fill: parent
            anchors.margins: -3
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onContainsMouseChanged: if (containsMouse) Ui.hideTip(pill)
            onClicked: {
                Ui.markTap()
                Ui.app.openFolderMenu(pill.path, menuGlyph, 0, menuGlyph.height)
            }
        }
    }
}
