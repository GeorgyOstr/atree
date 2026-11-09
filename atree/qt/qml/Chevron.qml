import QtQuick

// natural glyph width, never padded
Text {
    property bool open: false
    text: "›"
    color: Theme.muted
    font.pixelSize: 17
    verticalAlignment: Text.AlignVCenter
    rotation: open ? 90 : 0
    Behavior on rotation { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
}
