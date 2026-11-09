import QtQuick

// A static running indicator; the busy glow fades in and out but never loops.
Item {
    id: dot
    property real size: 8
    property bool busy: false
    width: size
    height: size

    Rectangle {
        anchors.centerIn: parent
        width: dot.size * 3
        height: width
        radius: width / 2
        color: Theme.live
        opacity: dot.busy ? 0.12 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.slow } }
    }
    Rectangle {
        anchors.centerIn: parent
        width: dot.size * 2
        height: width
        radius: width / 2
        color: Theme.live
        opacity: dot.busy ? 0.22 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: Theme.slow } }
    }
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: dot.busy ? Qt.lighter(Theme.live, 1.25) : Theme.live
    }
}
