pragma Singleton
import QtQuick

// Window-wide state shared by components living in different files.
QtObject {
    property var app: null
    property var tip: null
    property Item canvas: null
    property double childTapAt: 0

    function markTap() {
        childTapAt = Date.now()
        focusCanvas()
    }

    function recentChildTap() {
        return Date.now() - childTapAt < 250
    }

    function focusCanvas() {
        if (canvas && !canvas.activeFocus)
            canvas.forceActiveFocus()
    }

    function showTip(owner, text, delay) {
        if (tip)
            tip.ask(owner, text, delay)
    }

    function hideTip(owner) {
        if (tip)
            tip.drop(owner)
    }
}
