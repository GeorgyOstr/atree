import QtQuick

// Text field of the side panels and dialogs.
Rectangle {
    id: box
    property alias text: input.text
    property string placeholder: ""
    property bool invalid: false
    property bool focusAccent: true
    readonly property bool hasFocus: input.activeFocus
    property int fontSize: 12
    signal accepted()
    signal escaped()
    signal edited()

    height: 28
    radius: 6
    color: Theme.bg
    border.color: invalid ? Theme.bad : (focusAccent && input.activeFocus ? Theme.accent : Theme.line)
    clip: true

    function focusAll() {
        input.forceActiveFocus()
        input.selectAll()
    }

    TextInput {
        id: input
        objectName: "textBoxInput"
        anchors.fill: parent
        anchors.leftMargin: 8
        anchors.rightMargin: 8
        verticalAlignment: TextInput.AlignVCenter
        font.pixelSize: box.fontSize
        color: Theme.text
        selectionColor: Theme.textSelection
        selectedTextColor: "#ffffff"
        selectByMouse: true
        clip: true
        onAccepted: box.accepted()
        onTextEdited: box.edited()
        Keys.onEscapePressed: event => { event.accepted = true; box.escaped() }
    }
    Text {
        anchors.fill: input
        verticalAlignment: Text.AlignVCenter
        text: box.placeholder
        textFormat: Text.PlainText
        color: Theme.dim
        font.pixelSize: box.fontSize
        elide: Text.ElideRight
        visible: !input.text && !input.preeditText
    }
}
