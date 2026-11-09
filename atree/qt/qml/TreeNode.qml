import QtQuick

// One entry of the node model: a folder pill, a card or a connector.
Item {
    id: entry
    required property var model
    required property string kind
    z: kind === "link" ? (model.live > 0 ? -1 : -2) : 0

    Loader {
        active: entry.kind === "dir"
        sourceComponent: FolderPill { node: entry.model }
    }
    Loader {
        active: entry.kind === "card"
        sourceComponent: Card { node: entry.model }
    }
    Loader {
        active: entry.kind === "link"
        sourceComponent: Connector { node: entry.model }
    }
}
