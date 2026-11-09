import QtQuick
import QtQuick.Controls

// The one tooltip instance of the window: the stock Basic look, always plain text,
// centered above the hovered item.
ToolTip {
    id: tip
    objectName: "sharedTip"
    property Item owner: null
    property string pending: ""

    padding: 6
    margins: 6
    closePolicy: Popup.NoAutoClose
    background: Rectangle {
        color: Theme.panel3
        border.color: Theme.line
    }
    contentItem: Text {
        objectName: "sharedTipText"
        text: tip.text
        textFormat: Text.PlainText
        color: Theme.text
    }

    Timer {
        id: wait
        onTriggered: tip.reveal()
    }

    function ask(item, txt, delay) {
        if (!txt) {
            drop(item)
            return
        }
        owner = item
        pending = txt
        if (visible) {
            reveal()
            return
        }
        wait.interval = Math.max(1, delay)
        wait.restart()
    }

    function reveal() {
        if (!owner)
            return
        parent = owner
        text = pending
        open()
    }

    function drop(item) {
        if (item && item !== owner)
            return
        wait.stop()
        close()
        owner = null
    }
}
