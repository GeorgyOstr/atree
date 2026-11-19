import QtQuick
import QtQuick.Shapes

Shape {
    id: link
    preferredRendererType: Shape.GeometryRenderer
    antialiasing: true
    property var node
    property real x1: node.x1
    property real y1: node.y1
    property real x2: node.x2
    property real y2: node.y2
    readonly property bool live: node.live > 0
    // trunk: down from under the folder's chevron, then a rounded elbow into the branch
    readonly property bool trunk: node.trunk === true
    // a live trunk runs as its own rail left of the grey one, so the grey branches it passes
    // don't hang off a green line and look live
    readonly property real tx: trunk && live ? x1 - 4 : x1
    readonly property real radius: Math.max(0, Math.min(10, y2 - y1, x2 - tx))
    readonly property color stroke: live ? Qt.rgba(0.239, 0.863, 0.518, 0.7) : Theme.line
    Behavior on x1 { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on y1 { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on x2 { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
    Behavior on y2 { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }

    opacity: 0
    Component.onCompleted: opacity = 1
    Behavior on opacity { NumberAnimation { duration: Theme.slow } }

    ShapePath {
        strokeColor: link.trunk ? link.stroke : "transparent"
        strokeWidth: link.live ? 2 : 1.5
        fillColor: "transparent"
        capStyle: ShapePath.RoundCap
        startX: link.tx
        startY: link.y1
        PathLine { x: link.tx; y: link.y2 - link.radius }
        PathQuad { x: link.tx + link.radius; y: link.y2; controlX: link.tx; controlY: link.y2 }
        PathLine { x: link.x2; y: link.y2 }
    }

    ShapePath {
        strokeColor: link.trunk ? "transparent" : link.stroke
        strokeWidth: link.live ? 2 : 1.5
        fillColor: "transparent"
        startX: link.x1
        startY: link.y1
        PathCubic {
            x: link.x2
            y: link.y2
            control1X: (link.x1 + link.x2) / 2
            control1Y: link.y1
            control2X: (link.x1 + link.x2) / 2
            control2Y: link.y2
        }
    }
}
