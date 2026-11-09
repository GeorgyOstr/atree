import QtQuick

// Tool button (header, dialogs, details panel).
Rectangle {
    id: btn
    property string text: ""
    property string tip: ""
    property int tipDelay: 500
    property bool primary: false   // details panel: accent fill, bold dark text
    property bool accent: false    // dialogs' OK: accent fill, normal light text
    property bool enabledLook: true
    property int fontSize: 12
    property int hpad: 18          // width = text + hpad
    signal clicked()

    height: 24
    width: label.implicitWidth + hpad
    radius: 6
    opacity: enabledLook ? 1 : 0.6
    color: !enabledLook ? Theme.panel3
         : primary || accent ? (area.containsMouse ? Qt.lighter(Theme.accent, 1.1) : Theme.accent)
         : (area.containsMouse ? Theme.panel3 : Theme.panel2)
    border.color: (primary || accent) && enabledLook ? Theme.accent : Theme.line
    Behavior on color { ColorAnimation { duration: Theme.quick } }

    Text {
        id: label
        anchors.centerIn: parent
        text: btn.text
        textFormat: Text.PlainText
        font.pixelSize: btn.fontSize
        font.bold: btn.primary
        color: btn.primary && btn.enabledLook ? Theme.darkOnAccent : Theme.text
    }
    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        enabled: btn.enabledLook
        cursorShape: Qt.PointingHandCursor
        onClicked: { Ui.markTap(); Ui.hideTip(btn); btn.clicked() }
        onContainsMouseChanged: containsMouse && btn.tip ? Ui.showTip(btn, btn.tip, btn.tipDelay) : Ui.hideTip(btn)
    }
}
