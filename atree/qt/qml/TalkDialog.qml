import QtQuick
import QtQuick.Controls

// Modal dialog frame with a talk list and an OK / Cancel footer.
Popup {
    id: dlg
    property string title: ""
    property var talks: []
    property string okText: "OK"
    property bool okEnabled: true
    default property alias body: extra.data
    signal acceptedOk()

    modal: true
    focus: true
    width: 600
    padding: 0
    anchors.centerIn: Overlay.overlay
    closePolicy: Popup.CloseOnEscape
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 140 } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 100 } }
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.5) }
    background: Rectangle {
        color: Theme.panel
        border.color: Theme.line
        radius: 12
    }

    function accept() {
        if (!okEnabled)
            return
        close()
        acceptedOk()
    }

    contentItem: Column {
        spacing: 0
        Text {
            objectName: "dialogTitle"
            width: parent.width
            topPadding: 18
            leftPadding: 18
            rightPadding: 18
            bottomPadding: 6
            text: dlg.title
            textFormat: Text.PlainText
            elide: Text.ElideRight
            font.pixelSize: 15
            font.bold: true
            color: Theme.text
        }
        Column {
            width: parent.width
            topPadding: 8
            leftPadding: 18
            rightPadding: 18
            bottomPadding: 18
            spacing: 10
            ListView {
                objectName: "dialogTalks"
                width: parent.width - 36
                height: Math.min(dlg.talks.length, 6) * 22
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                model: dlg.talks
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
                delegate: Text {
                    required property var modelData
                    width: ListView.view.width
                    height: 22
                    verticalAlignment: Text.AlignVCenter
                    text: "•  " + modelData.title + "   —   " + modelData.folder
                    textFormat: Text.PlainText
                    elide: Text.ElideRight
                    font.pixelSize: 13
                    color: Theme.text
                }
            }
            Column {
                id: extra
                width: parent.width - 36
                spacing: 10
            }
        }
        Row {
            // OK at the far right, Cancel to its left
            width: parent.width
            layoutDirection: Qt.RightToLeft
            padding: 16
            topPadding: 4
            spacing: 8
            HButton {
                objectName: "dialogOk"
                text: dlg.okText
                accent: true
                enabledLook: dlg.okEnabled
                onClicked: dlg.accept()
            }
            HButton {
                text: "Cancel"
                onClicked: dlg.close()
            }
        }
    }
}
