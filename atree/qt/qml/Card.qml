import QtQuick
import QtQuick.Controls

Item {
    id: card
    objectName: "card"
    property var node
    readonly property string path: node.path
    readonly property bool folded: node.collapsed
    readonly property bool hovered: hover.hovered
    readonly property bool headHovered: headHover.hovered

    x: node.nx
    y: node.ny
    width: folded ? Math.min(node.nw, headRow.implicitWidth + 64) : node.nw
    height: node.nh
    Behavior on x { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on y { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on width { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on height { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }

    opacity: 0
    Component.onCompleted: opacity = 1
    Behavior on opacity { NumberAnimation { duration: Theme.slow } }

    HoverHandler { id: hover }

    Rectangle {
        anchors.fill: parent
        radius: 9
        color: card.node.drop ? Theme.selection : Theme.panel
    }

    // header
    Item {
        id: head
        width: parent.width
        height: Theme.headH
        HoverHandler { id: headHover }
        Rectangle {
            anchors.fill: parent
            radius: 9
            color: card.node.drop ? Theme.selection : (card.headHovered ? Theme.panel3 : Theme.panel2)
            Behavior on color { ColorAnimation { duration: Theme.quick } }
            Rectangle {
                visible: !card.folded
                anchors.bottom: parent.bottom
                width: parent.width
                height: 10
                color: parent.color
            }
        }
        Rectangle {
            visible: !card.folded
            anchors.bottom: parent.bottom
            width: parent.width
            height: 1
            color: Theme.line
        }
        MouseArea {
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton | Qt.RightButton
            cursorShape: Qt.PointingHandCursor
            hoverEnabled: true
            onClicked: mouse => {
                Ui.markTap()
                Ui.hideTip(head)
                if (mouse.button === Qt.RightButton)
                    Ui.app.openFolderMenu(card.path, head, mouse.x, mouse.y)
                else
                    backend.setCardMode(card.path, card.folded ? "compact" : "collapsed")
            }
            onContainsMouseChanged: {
                if (containsMouse && !Ui.canvas.dragging)
                    Ui.showTip(head, backend.tildify(card.path) + "\nclick to " + (card.folded ? "unfold" : "fold"), 900)
                else
                    Ui.hideTip(head)
            }
        }
        Row {
            id: headRow
            objectName: "cardHeadRow"
            x: 10
            anchors.verticalCenter: parent.verticalCenter
            spacing: 8
            Chevron {
                open: !card.folded
                anchors.verticalCenter: parent.verticalCenter
                anchors.verticalCenterOffset: -1
            }
            Text {
                id: labelText
                text: card.node.label
                textFormat: Text.PlainText
                visible: text !== ""
                font.pixelSize: 13
                font.bold: true
                color: Theme.text
                elide: Text.ElideMiddle
                // hovering adds the ▶ button on the right; leave it room
                width: Math.min(implicitWidth, Theme.cardW - 135 - (card.node.live > 0 ? 85 : 0) - (card.headHovered ? 26 : 0))
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                text: Theme.plural(card.node.talks, "talk")
                font.pixelSize: 12
                color: Theme.muted
                anchors.verticalCenter: parent.verticalCenter
            }
            Row {
                visible: card.node.live > 0
                spacing: 5
                anchors.verticalCenter: parent.verticalCenter
                LiveDot {
                    size: 7
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: card.node.live + " running"
                    font.pixelSize: 12
                    color: Theme.live
                    anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
        Row {
            objectName: "cardRight"
            anchors.right: parent.right
            anchors.rightMargin: 8
            anchors.verticalCenter: parent.verticalCenter
            spacing: 4
            Text {
                objectName: "cardAge"
                visible: !card.headHovered
                text: card.node.age
                font.pixelSize: 11
                color: Theme.dim
                anchors.verticalCenter: parent.verticalCenter
            }
            MiniButton {
                visible: card.headHovered && card.node.mode !== "full" && card.node.count > 6
                text: "Show all"
                textColor: Theme.text
                onClicked: backend.setCardMode(card.path, "full")
            }
            MiniButton {
                visible: card.headHovered && card.node.mode === "full"
                text: "Less"
                textColor: Theme.text
                onClicked: backend.setCardMode(card.path, "compact")
            }
            MiniButton {
                objectName: "cardPlay"
                visible: card.headHovered && backend.talkAgent.name !== undefined
                text: "▶"
                fontSize: 10
                tip: "New " + backend.talkAgent.label + " talk in this folder"
                onClicked: backend.newTalk(card.path)
            }
            MiniButton {
                id: moreBtn
                visible: card.headHovered
                text: "⋯"
                textColor: Theme.text
                onClicked: Ui.app.openFolderMenu(card.path, moreBtn, 0, moreBtn.height)
            }
        }
    }

    ListView {
        id: list
        objectName: "rowList"
        y: 34
        width: parent.width
        height: Math.max(0, card.height - 34 - 4)
        // stays up while the card's height animates, so a fold clips rows away
        visible: height >= 2
        clip: true
        interactive: false
        boundsBehavior: Flickable.StopAtBounds
        model: backend.cardModel(card.path)
        delegate: TalkRow {}
        readonly property bool overflowing: contentHeight > height + 1
        Behavior on contentY {
            id: glide
            enabled: false
            NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
        }
        ScrollBar.vertical: ThinScrollBar {
            objectName: "cardScrollBar"
            needed: list.overflowing
        }
    }

    MouseArea {
        anchors.fill: list
        enabled: list.visible
        acceptedButtons: Qt.NoButton
        onWheel: wheel => {
            if (!list.overflowing || wheel.modifiers !== Qt.NoModifier) {
                wheel.accepted = false
                return
            }
            var touch = wheel.phase !== Qt.NoScrollPhase && wheel.pixelDelta.y !== 0
            var dy = touch ? wheel.pixelDelta.y : wheel.angleDelta.y / 120 * 3 * Theme.rowH
            var maxY = list.contentHeight - list.height
            var to = Math.max(0, Math.min(maxY, list.contentY - dy))
            if (Math.abs(to - list.contentY) < 0.5) {
                wheel.accepted = false
                return
            }
            glide.enabled = !touch
            var moved = list.contentY - to
            list.contentY = to
            glide.enabled = false
            // past the top or bottom: the rest of this wheel step pans the canvas
            var rest = dy === 0 ? 0 : 1 - moved / dy
            if (rest > 0.01)
                Ui.canvas.wheelPan(wheel, rest)
        }
    }

    Rectangle {
        anchors.fill: parent
        radius: 9
        color: "transparent"
        border.color: card.node.drop ? Theme.accent : (card.hovered ? Theme.dim : Theme.line)
        border.width: card.node.drop ? 2 : 1
    }
}
