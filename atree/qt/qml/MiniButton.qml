import QtQuick

Rectangle {
    id: btn
    property string text: ""
    property color textColor: Theme.muted
    property string tip: ""
    property int fontSize: 12
    signal clicked()

    height: 20
    width: label.implicitWidth + 14
    radius: 5
    color: area.containsMouse ? Theme.line : "transparent"
    Behavior on color { ColorAnimation { duration: Theme.quick } }

    Text {
        id: label
        anchors.centerIn: parent
        text: btn.text
        textFormat: Text.PlainText
        font.pixelSize: btn.fontSize
        color: area.containsMouse ? Theme.text : btn.textColor
    }
    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: { Ui.markTap(); btn.clicked() }
        onContainsMouseChanged: containsMouse && btn.tip ? Ui.showTip(btn, btn.tip, 300) : Ui.hideTip(btn)
    }
}
