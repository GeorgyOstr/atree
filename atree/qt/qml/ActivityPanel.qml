import QtQuick

SidePanel {
    id: panel
    objectName: "activityPanel"
    readonly property var grid: backend.activity
    readonly property var weeks: grid.weeks || []
    readonly property var tools: grid.tools || []

    title: "Activity"
    note: "prompts per day"
    width: Math.max(320, matrices.implicitWidth + 24)
    bodyHeight: matrices.implicitHeight + 12
    fadeDuration: Theme.quick

    function cellColor(day, tool) {
        if (day.future)
            return "transparent"
        var n = day.n[tool] || 0
        if (n <= 0)
            return Theme.panel2
        var peak = (panel.grid.peak || {})[tool] || 1
        var step = Math.min(4, Math.ceil(4 * n / peak))
        var c = Qt.color(Theme.toolColor(tool))
        return Qt.rgba(c.r, c.g, c.b, [0, 0.3, 0.5, 0.75, 1][step])
    }

    Row {
        id: matrices
        objectName: "activityContent"
        x: 12
        spacing: 16

        Column {
            spacing: 2
            topPadding: 30
            Repeater {
                model: ["M", "T", "W", "T", "F", "S", "S"]
                Text {
                    required property string modelData
                    height: 13
                    width: 9
                    text: modelData
                    font.pixelSize: 9
                    color: Theme.dim
                    verticalAlignment: Text.AlignVCenter
                }
            }
        }

        Repeater {
            model: panel.tools
            Column {
                id: matrix
                required property var modelData
                readonly property string tool: modelData.name
                Row {
                    height: 16
                    spacing: 5
                    Rectangle {
                        width: 8
                        height: 8
                        radius: 2
                        color: Theme.toolColor(matrix.tool)
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: matrix.modelData.label
                        font.pixelSize: 11
                        font.bold: true
                        color: Theme.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Item {
                    width: cells.implicitWidth
                    height: 14
                    Repeater {
                        model: panel.grid.months || []
                        Text {
                            required property var modelData
                            x: modelData.week * 15
                            text: modelData.name
                            font.pixelSize: 10
                            color: Theme.muted
                        }
                    }
                }
                Row {
                    id: cells
                    spacing: 2
                    Repeater {
                        model: panel.weeks
                        Column {
                            id: week
                            objectName: "activityWeek"
                            required property var modelData
                            spacing: 2
                            Repeater {
                                model: week.modelData
                                Rectangle {
                                    id: cell
                                    objectName: "activityCell"
                                    required property var modelData
                                    readonly property bool inside: modelData.inside
                                    width: 13
                                    height: 13
                                    radius: 2
                                    opacity: inside ? 1 : 0
                                    color: inside ? panel.cellColor(modelData, matrix.tool) : "transparent"
                                    border.width: inside && (modelData.today || cellArea.containsMouse) ? 1
                                                : (inside && modelData.future ? 1 : 0)
                                    border.color: modelData.future && !modelData.today && !cellArea.containsMouse
                                                  ? Theme.panel2 : Theme.text
                                    MouseArea {
                                        id: cellArea
                                        anchors.fill: parent
                                        enabled: cell.inside
                                        hoverEnabled: cell.inside
                                        onPressed: Ui.markTap()
                                        onContainsMouseChanged: {
                                            if (containsMouse && !cell.modelData.future) {
                                                var n = cell.modelData.n[matrix.tool] || 0
                                                Ui.showTip(cell, cell.modelData.label + (cell.modelData.today ? " (today)" : "")
                                                           + "\n" + matrix.modelData.label + ": "
                                                           + Number(n).toLocaleString(Qt.locale(), "f", 0)
                                                           + (n === 1 ? " prompt" : " prompts"), 0)
                                            } else {
                                                Ui.hideTip(cell)
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                Row {
                    // month totals side by side, not under their month's columns
                    objectName: "activityTotals"
                    topPadding: 6
                    spacing: 10
                    Repeater {
                        model: panel.grid.months || []
                        Text {
                            required property var modelData
                            text: modelData.name + " " + Number(modelData.totals[matrix.tool] || 0).toLocaleString(Qt.locale(), "f", 0)
                            font.pixelSize: 10
                            color: Theme.muted
                        }
                    }
                }
            }
        }
    }
}
