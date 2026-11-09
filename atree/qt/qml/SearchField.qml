import QtQuick

Rectangle {
    id: field
    objectName: "searchField"
    // typing filters by text; Enter asks the leftmost agent that can search
    readonly property var aiAgent: backend.searchAgent

    // a standard text field: 6 px above and below 13 px text
    height: Math.round(input.implicitHeight) + 12
    radius: 7
    color: Theme.bg
    border.color: input.activeFocus ? Theme.accent : Theme.line

    function focusField() {
        input.forceActiveFocus()
        input.selectAll()
    }

    function clearAll() {
        input.text = ""
        debounce.stop()
        backend.setFilter("q", "")
        backend.clearAi()
    }

    TextInput {
        id: input
        objectName: "searchInput"
        anchors.left: parent.left
        anchors.leftMargin: 10
        anchors.right: busyText.visible ? busyText.left : parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        font.pixelSize: 13
        color: Theme.text
        selectionColor: Theme.textSelection
        selectedTextColor: "#ffffff"
        selectByMouse: true
        clip: true
        onTextEdited: {
            // AI results would hide the text filter; editing goes back to it
            if (backend.ai.busy !== true)
                backend.clearAi()
            debounce.restart()
        }
        onAccepted: {
            debounce.stop()
            backend.setFilter("q", text)
            if (field.aiAgent.name)
                backend.aiSearch(field.aiAgent.name, text)
        }
        Keys.onEscapePressed: event => {
            event.accepted = true
            field.clearAll()
            Ui.focusCanvas()
        }
    }
    Text {
        anchors.fill: input
        verticalAlignment: Text.AlignVCenter
        visible: !input.text && !input.preeditText
        text: "Search talks…   ( / )" + (field.aiAgent.name ? "   ·   Enter: ask " + field.aiAgent.label : "")
        textFormat: Text.PlainText
        color: Theme.dim
        font.pixelSize: 13
        elide: Text.ElideRight
    }
    Text {
        id: busyText
        visible: backend.ai.busy === true
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        text: "searching…"
        font.pixelSize: 11
        color: Theme.accent
    }
    Timer {
        id: debounce
        interval: 150
        onTriggered: backend.setFilter("q", input.text)
    }
}
