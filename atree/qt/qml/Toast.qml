import QtQuick

Rectangle {
    id: toast
    objectName: "toast"
    property string message: ""
    property bool isError: false
    property var undoKeys: []
    property real maxWidth: 600

    width: Math.min(line.implicitWidth + 28, maxWidth)
    height: line.implicitHeight + 16
    radius: 8
    color: Theme.panel2
    border.color: isError ? Theme.bad : Theme.line
    opacity: shownNow ? 1 : 0
    visible: opacity > 0
    property bool shownNow: false
    Behavior on opacity { NumberAnimation { duration: 200 } }

    function show(text, error, keys) {
        message = text
        isError = error
        undoKeys = keys || []
        shownNow = true
        hideTimer.interval = error ? 7000 : (undoKeys.length > 0 ? 3000 : 2600)
        hideTimer.restart()
    }

    Timer {
        id: hideTimer
        onTriggered: toast.shownNow = false
    }

    Row {
        id: line
        anchors.centerIn: parent
        spacing: 14
        Text {
            id: msg
            objectName: "toastText"
            width: Math.min(implicitWidth, toast.maxWidth - 28 - (undoBtn.visible ? undoBtn.width + 14 : 0))
            anchors.verticalCenter: parent.verticalCenter
            text: toast.message
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            font.pixelSize: 13
            color: toast.isError ? Theme.errorText : Theme.text
        }
        HButton {
            id: undoBtn
            objectName: "toastUndo"
            visible: toast.undoKeys.length > 0
            anchors.verticalCenter: parent.verticalCenter
            text: "Undo"
            onClicked: {
                var keys = toast.undoKeys
                toast.undoKeys = []
                hideTimer.stop()
                toast.shownNow = false
                backend.restore(keys)
            }
        }
    }
}
