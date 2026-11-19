import QtQuick

// What the last compression changed: each merged talk and the talks that went into it,
// then, while there is room under `limit`, the lessons and suggestions drawn from them.
Rectangle {
    id: panel
    objectName: "mergePanel"
    property real limit: 300
    readonly property var report: backend.compressReport
    readonly property var items: report.items || []
    readonly property var errors: report.errors || []
    readonly property var lessons: {
        var out = []
        for (var i = 0; i < items.length; i++)
            out = out.concat(items[i].lessons || [])
        return out
    }
    // lines of lessons that still fit once the changes are shown
    readonly property int lessonLines: Math.max(0, Math.floor(
        (limit - 16 - core.implicitHeight - body.spacing - lessonsHead.height) / probe.implicitHeight))

    visible: items.length > 0
    height: visible ? body.implicitHeight + 16 : 0
    color: Theme.panel
    border.color: Theme.line
    radius: 10
    clip: true

    MouseArea {
        anchors.fill: parent
        onPressed: Ui.markTap()
    }
    Text {
        id: probe
        visible: false
        font.pixelSize: 11
        text: "X"
    }

    Column {
        id: body
        x: 8
        y: 8
        width: parent.width - 16
        spacing: 4

        Column {
            id: core
            width: parent.width
            spacing: 2
            Item {
                width: parent.width
                height: 22
                Text {
                    objectName: "mergeHead"
                    anchors.left: parent.left
                    anchors.right: clearText.left
                    anchors.rightMargin: 8
                    anchors.verticalCenter: parent.verticalCenter
                    elide: Text.ElideRight
                    textFormat: Text.PlainText
                    font.pixelSize: 12
                    font.bold: true
                    color: Theme.text
                    text: "✦ " + (panel.report.agent || "") + " merged " + (panel.report.talks || 0)
                          + " talks into " + panel.items.length + " · " + (panel.report.folder || "")
                }
                Text {
                    id: clearText
                    objectName: "mergeClear"
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    text: "clear ✕"
                    font.pixelSize: 11
                    color: clearArea.containsMouse ? Theme.text : Theme.dim
                    MouseArea {
                        id: clearArea
                        anchors.fill: parent
                        anchors.margins: -3
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { Ui.markTap(); backend.clearCompressReport() }
                    }
                }
            }
            Repeater {
                model: panel.items
                Rectangle {
                    id: res
                    objectName: "mergeItem"
                    required property var modelData
                    required property int index
                    readonly property var sources: modelData.sources || []
                    width: core.width
                    height: lines.implicitHeight + 8
                    radius: 6
                    color: resArea.containsMouse ? Theme.panel2 : "transparent"
                    Rectangle {
                        x: 6
                        y: 9
                        width: 8
                        height: 8
                        radius: 4
                        color: Theme.toolColor(res.modelData.tool)
                    }
                    Column {
                        id: lines
                        x: 20
                        y: 4
                        width: parent.width - 26
                        spacing: 1
                        Text {
                            objectName: "mergeTitle"
                            width: parent.width
                            text: res.modelData.title
                            textFormat: Text.PlainText
                            elide: Text.ElideRight
                            font.pixelSize: 12
                            color: Theme.text
                        }
                        Text {
                            objectName: "mergeSources"
                            width: parent.width
                            visible: res.sources.length > 0
                            text: "← " + res.sources.join("  ·  ")
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            font.pixelSize: 11
                            color: Theme.muted
                        }
                        Text {
                            objectName: "mergeBrief"
                            width: parent.width
                            visible: text !== ""
                            text: res.modelData.brief || ""
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                            font.pixelSize: 11
                            color: Theme.promptText
                        }
                    }
                    MouseArea {
                        id: resArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: { Ui.markTap(); backend.select(res.modelData.key, false) }
                        onContainsMouseChanged: containsMouse
                            ? Ui.showTip(res, [res.modelData.title, res.modelData.brief || "",
                                               "from: " + res.sources.join(" · ")]
                                              .concat((res.modelData.lessons || []).map(l => "• " + l))
                                              .filter(t => t !== "").join("\n"), 600)
                            : Ui.hideTip(res)
                    }
                }
            }
            Text {
                objectName: "mergeErrors"
                width: parent.width
                visible: panel.errors.length > 0
                text: panel.errors.join("\n")
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: 3
                elide: Text.ElideRight
                font.pixelSize: 11
                color: Theme.bad
            }
        }
        Column {
            objectName: "mergeLessons"
            width: parent.width
            visible: panel.lessons.length > 0 && panel.lessonLines > 0
            spacing: 0
            Text {
                id: lessonsHead
                x: 6
                height: implicitHeight + 2
                text: "Lessons & suggestions"
                font.pixelSize: 11
                font.bold: true
                color: Theme.dim
            }
            Text {
                objectName: "mergeLessonsText"
                x: 6
                width: parent.width - 12
                text: panel.lessons.map(l => "• " + l).join("\n")
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                maximumLineCount: Math.max(1, panel.lessonLines)
                elide: Text.ElideRight
                font.pixelSize: 11
                color: Theme.muted
                MouseArea {
                    id: lessonsArea
                    anchors.fill: parent
                    hoverEnabled: true
                    onContainsMouseChanged: containsMouse
                        ? Ui.showTip(parent, parent.text, 600) : Ui.hideTip(parent)
                }
            }
        }
    }
}
