import QtQuick

Text {
    id: btn
    signal clicked()
    text: "✕"
    font.pixelSize: 12
    color: area.containsMouse ? Theme.bad : Theme.muted
    MouseArea {
        id: area
        anchors.fill: parent
        anchors.margins: -4
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: { Ui.markTap(); btn.clicked() }
    }
}
