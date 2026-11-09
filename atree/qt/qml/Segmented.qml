import QtQuick

Rectangle {
    id: seg
    property var options: []      // [{label, value}]
    property var current
    property string tip: ""
    signal picked(var value)

    height: 24
    width: row.implicitWidth + 4
    radius: 7
    color: Theme.bg
    border.color: Theme.line

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 0
        Repeater {
            model: seg.options
            Rectangle {
                id: cell
                required property var modelData
                readonly property bool chosen: modelData.value === seg.current
                height: 20
                width: cellText.implicitWidth + 16
                radius: 5
                color: chosen ? Theme.panel3 : (cellArea.containsMouse ? Theme.panel2 : "transparent")
                Behavior on color { ColorAnimation { duration: Theme.quick } }
                Text {
                    id: cellText
                    anchors.centerIn: parent
                    text: cell.modelData.label
                    font.pixelSize: 12
                    color: cell.chosen ? Theme.text : Theme.muted
                }
                MouseArea {
                    id: cellArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { Ui.markTap(); seg.picked(cell.modelData.value) }
                    onContainsMouseChanged: containsMouse && seg.tip ? Ui.showTip(seg, seg.tip, 600) : Ui.hideTip(seg)
                }
            }
        }
    }
}
