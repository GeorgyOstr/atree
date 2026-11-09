import QtQuick
import QtQuick.Layouts

Rectangle {
    id: header
    objectName: "header"
    property alias searchField: search
    property real leftInset: 0
    property real rightInset: 0
    property alias logoIcon: logoIcon
    // widget mode, desktop given the clicks: everything but the logo fades
    property bool faded: false
    property real fade: faded ? 0.15 : 1
    Behavior on fade { NumberAnimation { duration: Theme.slow } }
    signal fitRequested()

    // one 66 px band when everything fits: chips + tool buttons over the age/sort toolbar, usage
    // blocks on the right spanning both; a narrow window moves the tools and usage below it
    readonly property real oneRowWidth: 28 + leftInset + rightInset + logo.implicitWidth + 14 + 200 + 14
                                        + Math.max(chipRow.implicitWidth + 5 + tools.implicitWidth, filters.implicitWidth)
                                        + 14 + usageRow.implicitWidth
    readonly property bool twoRows: width < oneRowWidth
    height: twoRows ? 66 + band.implicitHeight + 10 : 66
    color: desktopWidget ? Qt.rgba(0.090, 0.106, 0.133, 0.85 * fade) : Theme.panel

    MouseArea {
        anchors.fill: parent
        onPressed: Ui.markTap()
    }

    Rectangle {
        anchors.bottom: parent.bottom
        width: parent.width
        height: 1
        color: Theme.line
        opacity: header.fade
    }

    Flow {
        id: band
        objectName: "headerBand"
        opacity: header.fade
        visible: header.twoRows
        x: 14 + header.leftInset
        y: 66
        width: header.width - 28 - header.leftInset - header.rightInset
        spacing: 22
        Item { id: toolsSlot; width: childrenRect.width; height: childrenRect.height }
        Item { id: usageSlot; width: childrenRect.width; height: childrenRect.height }
    }

    property alias oneRow: oneRowLayout

    RowLayout {
        id: oneRowLayout
        anchors.left: parent.left
        anchors.right: parent.right
        height: 66
        anchors.leftMargin: 14 + header.leftInset
        anchors.rightMargin: 14 + header.rightInset
        spacing: 14

        Row {
            id: logo
            spacing: 8
            Layout.alignment: Qt.AlignVCenter
            Image {
                id: logoIcon
                objectName: "logoIcon"
                source: "icon.svg"
                width: 40
                height: 40
                sourceSize: Qt.size(120, 120)
                smooth: true
                mipmap: true
                anchors.verticalCenter: parent.verticalCenter
                scale: logoArea.containsMouse ? 1.12 : 1
                Behavior on scale { NumberAnimation { duration: Theme.quick } }
                MouseArea {
                    id: logoArea
                    enabled: desktopWidget
                    anchors.fill: parent
                    anchors.margins: -4
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: { Ui.markTap(); Ui.app.toggleDesktopPass() }
                    onContainsMouseChanged: containsMouse
                        ? Ui.showTip(logoIcon, header.faded ? "Click to bring atree back"
                                                            : "Click to fade atree and use the desktop", 300)
                        : Ui.hideTip(logoIcon)
                }
            }
            // the name greets for 2 s, then gives way to user@host
            Item {
                id: brand
                objectName: "brand"
                opacity: header.fade
                property bool greeting: true
                // the desktop widget shows the logo alone
                visible: !desktopWidget
                width: Math.max(name.implicitWidth, who.implicitWidth)
                height: 24
                anchors.verticalCenter: parent.verticalCenter
                Timer { interval: 2000; running: true; onTriggered: brand.greeting = false }
                Text {
                    id: name
                    objectName: "brandName"
                    text: "atree"
                    font.pixelSize: 15
                    font.bold: true
                    color: Theme.text
                    anchors.verticalCenter: parent.verticalCenter
                    opacity: brand.greeting ? 1 : 0
                    visible: opacity > 0
                    Behavior on opacity { NumberAnimation { duration: 250 } }
                }
                Text {
                    id: who
                    objectName: "brandWho"
                    text: backend.whoami.user + "@" + backend.whoami.host
                    textFormat: Text.PlainText
                    font.pixelSize: 15
                    font.bold: true
                    color: Theme.text
                    anchors.verticalCenter: parent.verticalCenter
                    opacity: brand.greeting ? 0 : 1
                    visible: opacity > 0
                    Behavior on opacity { NumberAnimation { duration: 250 } }
                }
            }
        }

        SearchField {
            id: search
            opacity: header.fade
            Layout.preferredWidth: 320
            Layout.maximumWidth: 320
            Layout.minimumWidth: 200
            Layout.fillWidth: true
            Layout.alignment: Qt.AlignVCenter
        }

        Column {
            id: controls
            opacity: header.fade
            spacing: 5
            Layout.alignment: Qt.AlignVCenter
            Layout.preferredHeight: 66 - 2 * 9
            // filter chips, then the tool buttons (Fit, Fold all, Unfold all, Auto)
            Row {
                id: chipLine
                spacing: 5
                Row {
                    id: chipRow
                    spacing: 5
                    Repeater {
                        id: chipRep
                        model: backend.agents
                        Chip {
                            required property var modelData
                            required property int index
                            objectName: "agentChip"
                            text: modelData.label
                            hasDot: true
                            dotColor: modelData.color
                            on: backend.filters[modelData.name] !== false
                            onClicked: backend.setFilter(modelData.name, !on)
                            draggable: true
                            dragging: chipOrder.dragFrom === index
                            shift: chipOrder.shiftFor(index)
                            onDragStarted: chipOrder.begin(index)
                            onDragMoved: dx => chipOrder.move(dx)
                            onDragEnded: chipOrder.release()
                        }
                    }
                    AgentOrder {
                        id: chipOrder
                        repeater: chipRep
                        spacing: chipRow.spacing
                        names: backend.agents.map(a => a.name)
                    }
                    Chip {
                        text: "Running only"
                        on: backend.filters.live === true
                        onClicked: backend.setFilter("live", !on)
                    }
                    Chip {
                        text: "Archived"
                        on: backend.filters.archived === true
                        onClicked: backend.setFilter("archived", !on)
                    }
                }
            }
            Row {
                id: filterRow
                objectName: "headerToolbar"
                spacing: 5
                Row {
                    id: filters
                    spacing: 5
                    Segmented {
                        options: [{ label: "24h", value: 1 }, { label: "7d", value: 7 }, { label: "30d", value: 30 },
                                  { label: "90d", value: 90 }, { label: "All time", value: 0 }]
                        current: backend.filters.age
                        onPicked: value => backend.setFilter("age", value)
                    }
                    Item { width: 6; height: 1 }
                    Segmented {
                        options: [{ label: "Recent", value: "recent" }, { label: "Context", value: "context" },
                                  { label: "Tokens", value: "tokens" }, { label: "Prompts", value: "prompts" },
                                  { label: "A–Z", value: "name" }]
                        current: backend.filters.sort
                        tip: "Sort talks and folders: last activity, context size, tokens used, prompt count, name"
                        onPicked: value => backend.setFilter("sort", value)
                    }
                    Chip {
                        text: "↑↓ Reverse"
                        on: backend.filters.reverse === true
                        onClicked: backend.setFilter("reverse", !on)
                    }
                }
            }
            Row {
                id: tools
                objectName: "headerTools"
                parent: header.twoRows ? toolsSlot : chipLine
                onParentChanged: { x = 0; y = 0 }
                spacing: 5
                Item { width: 6; height: 1 }
                HButton {
                    text: "Fit"
                    tip: "Fit tree to window  (F, or double-click empty space)"
                    onClicked: header.fitRequested()
                }
                HButton {
                    text: "Fold all"
                    tip: "Fold every folder and card"
                    onClicked: backend.setAllUnder("/", true)
                }
                HButton {
                    text: "Unfold all"
                    tip: "Unfold every folder and card"
                    onClicked: backend.setAllUnder("/", false)
                }
                HButton {
                    text: "Auto"
                    tip: "Forget manual folding: recent things open, stale things folded"
                    onClicked: backend.autoFold()
                }
            }
        }

        Item { Layout.fillWidth: true; Layout.minimumWidth: 0 }

        // one block per agent, side by side, centred across the chip and toolbar rows
        Row {
            id: usageRow
            opacity: header.fade
            objectName: "usageRow"
            parent: header.twoRows ? usageSlot : header.oneRow
            onParentChanged: if (header.twoRows) { x = 0; y = 0 }
            spacing: 22
            Layout.alignment: Qt.AlignVCenter
            Layout.minimumWidth: implicitWidth
            Repeater {
                id: usageRep
                model: backend.usage
                UsageGroup {
                    required property var modelData
                    required property int index
                    data_: modelData
                    dragging: usageOrder.dragFrom === index
                    shift: usageOrder.shiftFor(index)
                    onDragStarted: usageOrder.begin(index)
                    onDragMoved: dx => usageOrder.move(dx)
                    onDragEnded: usageOrder.release()
                }
            }
            AgentOrder {
                id: usageOrder
                repeater: usageRep
                spacing: usageRow.spacing
                names: backend.usage.map(g => g.key)
            }
        }
    }
}
