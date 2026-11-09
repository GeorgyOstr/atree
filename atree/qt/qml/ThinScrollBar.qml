import QtQuick
import QtQuick.Controls

// Draggable 8 px scrollbar with a 4 px handle, shown only while there is something to scroll.
ScrollBar {
    id: sb
    property bool needed: size < 1
    policy: needed ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
    width: 8
    padding: 2
    minimumSize: 0
    opacity: active ? 1 : 0.5
    background: null
    contentItem: Rectangle {
        implicitWidth: 4
        radius: 2
        color: sb.pressed ? Theme.muted : Theme.dim
    }
}
