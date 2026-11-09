import QtQuick
import QtQuick.Controls

// In-window context menu. Items: {text, hint, danger, visible, run}.
Popup {
    id: menu
    property string header: ""
    property var entries: []
    property int current: -1    // highlighted entry, by hover or keys
    readonly property real maximumWidth: Math.max(0, Math.min(480, Overlay.overlay ? Overlay.overlay.width - 16 : 480))

    padding: 0
    topPadding: 5
    bottomPadding: 5
    width: Math.min(maximumWidth, col.naturalWidth)
    modal: false
    focus: true
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 110 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 90 } }
    background: Rectangle {
        color: Theme.panel
        border.color: Theme.line
        radius: 9
    }

    function popupAt(item, px, py, title, list) {
        header = title || ""
        entries = list
        current = -1
        var p = item.mapToItem(Overlay.overlay, px, py)
        parent = Overlay.overlay
        col.forceLayout()
        var w = width, h = col.implicitHeight + topPadding + bottomPadding
        var ow = Overlay.overlay.width, oh = Overlay.overlay.height
        // flip to the other side of the point when there's no room, then shift inside
        var nx = p.x + w > ow ? p.x - w : p.x
        var ny = p.y + h > oh ? p.y - h : p.y
        x = Math.max(0, Math.min(nx, ow - w))
        y = Math.max(0, Math.min(ny, oh - h))
        open()
        col.forceActiveFocus()
    }

    function step(dir) {
        var n = entries.length
        for (var k = 1; k <= n; k++) {
            var i = ((current < 0 ? (dir > 0 ? -1 : n) : current) + dir * k + n * 2) % n
            if (entries[i].visible !== false) {
                current = i
                return
            }
        }
    }

    function activate(i) {
        if (i < 0 || i >= entries.length || entries[i].visible === false)
            return
        Ui.markTap()
        var run = entries[i].run
        close()
        if (run)
            run()
    }

    contentItem: Column {
        id: col
        readonly property real naturalWidth: {
            var w = 240
            for (var i = 0; i < children.length; i++) {
                if (children[i].shown !== false)
                    w = Math.max(w, children[i].implicitWidth)
            }
            return w
        }
        focus: true
        Keys.onUpPressed: menu.step(-1)
        Keys.onDownPressed: menu.step(1)
        Keys.onReturnPressed: menu.activate(menu.current)
        Keys.onEnterPressed: menu.activate(menu.current)
        Text {
            visible: menu.header !== ""
            width: menu.width
            height: 26
            leftPadding: 12
            rightPadding: 12
            verticalAlignment: Text.AlignVCenter
            text: menu.header
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.pixelSize: 12
            color: Theme.dim
        }
        Repeater {
            model: menu.entries
            Item {
                id: entry
                objectName: "menuEntry"
                required property var modelData
                required property int index
                readonly property bool shown: modelData.visible !== false
                visible: shown
                implicitWidth: label.implicitWidth + hint.implicitWidth + 48
                width: menu.width
                height: shown ? 30 : 0
                Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: 5
                    anchors.rightMargin: 5
                    radius: 6
                    color: menu.current === entry.index ? Theme.panel3 : "transparent"
                }
                Text {
                    id: label
                    x: 12
                    width: Math.max(0, parent.width - hint.implicitWidth - 36)
                    anchors.verticalCenter: parent.verticalCenter
                    text: entry.modelData.text
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: 13
                    color: entry.modelData.danger ? Theme.bad : Theme.text
                }
                Text {
                    id: hint
                    anchors.right: parent.right
                    anchors.rightMargin: 12
                    anchors.verticalCenter: parent.verticalCenter
                    text: entry.modelData.hint || ""
                    font.pixelSize: 11
                    color: Theme.dim
                }
                MouseArea {
                    id: itemArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onContainsMouseChanged: if (containsMouse) menu.current = entry.index
                    onClicked: menu.activate(entry.index)
                }
            }
        }
    }
}
