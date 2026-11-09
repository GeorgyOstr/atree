pragma Singleton
import QtQuick

QtObject {
    readonly property color bg: "#0f1217"
    readonly property color panel: "#171b22"
    readonly property color panel2: "#1e232c"
    readonly property color panel3: "#272d38"
    readonly property color line: "#2c3340"
    readonly property color text: "#e6e9f0"
    readonly property color muted: "#8a93a6"
    readonly property color dim: "#5f6779"
    readonly property color live: "#3ddc84"
    readonly property color warn: "#f2b84b"
    readonly property color bad: "#f2635b"
    readonly property color accent: "#b48cff"
    readonly property color selection: "#30264a"
    // selected text inside text boxes: must stand out on the dark field
    readonly property color textSelection: Qt.rgba(0.706, 0.549, 1.0, 0.45)
    readonly property color stale: "#aab1c0"
    readonly property color errorText: "#ffc9c5"
    readonly property color promptText: "#cfd4df"
    readonly property color darkOnAccent: "#16121f"

    readonly property int slow: 240
    readonly property int quick: 120

    readonly property int pillH: 28
    readonly property int cardW: 430
    readonly property int headH: 30
    readonly property int rowH: 26

    readonly property var agentColors: ({
        "claude": "#da724b", "codex": "#5b8cff", "gemini": "#2f9e7a",
        "opencode": "#b06ccf", "copilot": "#b08a14"
    })

    function toolColor(name) {
        return agentColors[name] !== undefined ? agentColors[name] : muted
    }

    function usageColor(left) {
        return left > 50 ? live : (left > 20 ? warn : bad)
    }

    function plural(n, word) {
        return n + " " + word + (n === 1 ? "" : "s")
    }
}
