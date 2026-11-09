import QtQuick

// The pannable / zoomable world holding the tree, with its scrollbars and drag-and-drop.
Item {
    id: canvas
    objectName: "treeCanvas"
    focus: true
    clip: true

    property real treeW: width
    property var view                        // persisted view settings
    property bool popupsOpen: false
    readonly property real cw: backend.contentWidth
    readonly property real ch: backend.contentHeight
    readonly property real minX: Math.min(40, treeW - cw * world.scale - 40)
    readonly property real minY: Math.min(40, height - ch * world.scale - 70)
    readonly property real maxX: 40
    readonly property real maxY: 40

    property bool panning: false
    property bool fitting: fitAnim.running
    property bool initialFitDone: false

    // drag-and-drop move
    property bool dragging: false
    property var dragKeys: []
    property string dragLabel: ""
    property var dragPoint: null
    property string dropPath: ""
    property real autoDx: 0
    property real autoDy: 0

    readonly property bool pointerInTree: hover.hovered && hover.point.position.x < treeW
    readonly property bool layoutHeld: pointerInTree || dragging || panning || pinch.active
                                       || vbar.pressed || hbar.pressed || popupsOpen

    onLayoutHeldChanged: {
        if (layoutHeld) {
            release.stop()
            backend.holdLayout(true)
        } else {
            release.restart()
        }
    }
    Timer {
        id: release
        interval: 800
        onTriggered: backend.holdLayout(false)
    }

    // left alone for a minute (no pointer in the tree, no drag, pan or menu): re-sort by real
    // recency if the stable view held an old order, and fit; repeats each idle minute, and
    // draws nothing when there is nothing to change
    property int idleFitAfter: 60000
    Timer {
        id: idleFit
        interval: canvas.idleFitAfter
        repeat: true
        running: !canvas.layoutHeld
        onTriggered: canvas.idleRefit()
    }
    function idleRefit() {
        if (layoutHeld || fitting || cw <= 0)
            return
        fit(backend.orderIsStale())
    }

    onMinXChanged: clampSoon()
    onMinYChanged: clampSoon()
    function clampSoon() {
        if (!fitAnim.running && !glide.running)
            panTo(world.x, world.y)
    }

    function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)) }

    function panTo(x, y) {
        world.x = clamp(x, minX, maxX)
        world.y = clamp(y, minY, maxY)
    }

    function panBy(dx, dy) {
        glide.stop()
        panTo(world.x + dx, world.y + dy)
    }

    function saveView() {
        if (!view)
            return
        view.x = world.x
        view.y = world.y
        view.scale = world.scale
        view.saved = true
    }

    function zoomAt(px, py, factor) {
        var s = clamp(world.scale * factor, 0.3, 2.5)
        var wx = (px - world.x) / world.scale
        var wy = (py - world.y) / world.scale
        world.scale = s
        panTo(px - wx * s, py - wy * s)
        saveTimer.restart()
    }

    function resetZoom() {
        world.scale = 1
        panTo(world.x, world.y)
        saveView()
    }

    // Fit: readable scale between 80 % and 100 %, top-left. Centred on a point (double-click on
    // empty space): the whole width fits too, down to the 30 % zoom floor, and the point's row
    // comes to the middle.
    function fit(apply, px, py) {
        if (apply)
            backend.applyLayout()
        if (cw <= 0)
            return
        var atPoint = px !== undefined && px !== null
        var s = clamp((treeW - 80) / cw, atPoint ? 0.3 : 0.8, 1)
        var loX = Math.min(40, treeW - cw * s - 40)
        var loY = Math.min(40, height - ch * s - 70)
        var tx = 40, ty = 40
        if (atPoint) {
            let wx = (px - world.x) / world.scale
            let wy = (py - world.y) / world.scale
            // fits: left edge at 40 like Fit; only a tree wider than 30 % allows centres on x
            tx = cw * s <= treeW - 80 + 0.5 ? 40 : clamp(treeW / 2 - wx * s, loX, 40)
            ty = clamp(height / 2 - wy * s, loY, 40)
        }
        glide.stop()
        fitAnim.stop()
        fx.to = tx
        fy.to = ty
        fs.to = s
        if (view) {
            view.x = tx
            view.y = ty
            view.scale = s
            view.saved = true
        }
        // already there: no animation, so an idle refit with nothing new renders no frames
        if (Math.abs(world.x - tx) < 0.5 && Math.abs(world.y - ty) < 0.5 && Math.abs(world.scale - s) < 0.001)
            return
        fitAnim.start()
    }

    // the first time there is content, fit unless a view was ever saved
    function firstFit() {
        if (initialFitDone || cw <= 0 || width <= 0)
            return
        initialFitDone = true
        if (!view || !view.saved)
            fit(false)
    }
    Connections {
        target: backend
        function onChanged() { canvas.firstFit() }
    }
    Component.onCompleted: Qt.callLater(firstFit)

    ParallelAnimation {
        id: fitAnim
        NumberAnimation { id: fx; target: world; property: "x"; duration: 320; easing.type: Easing.OutCubic }
        NumberAnimation { id: fy; target: world; property: "y"; duration: 320; easing.type: Easing.OutCubic }
        NumberAnimation { id: fs; target: world; property: "scale"; duration: 320; easing.type: Easing.OutCubic }
    }

    property real glideX: 0
    property real glideY: 0
    ParallelAnimation {
        id: glide
        NumberAnimation { id: gx; target: world; property: "x"; duration: 160; easing.type: Easing.OutCubic }
        NumberAnimation { id: gy; target: world; property: "y"; duration: 160; easing.type: Easing.OutCubic }
    }
    function glideBy(dx, dy) {
        var bx = glide.running ? glideX : world.x
        var by = glide.running ? glideY : world.y
        glideX = clamp(bx + dx, minX, maxX)
        glideY = clamp(by + dy, minY, maxY)
        glide.stop()
        gx.to = glideX
        gy.to = glideY
        glide.start()
    }

    Timer {
        id: saveTimer
        interval: 400
        onTriggered: canvas.saveView()
    }

    // drag-and-drop ------------------------------------------------------
    function startDrag(keys, title) {
        dragKeys = keys
        dragLabel = keys.length > 1 ? keys.length + " talks" : (title.length > 40 ? title.substring(0, 39) + "…" : title)
        dragging = true
    }

    function dragTo(sx, sy) {
        if (!dragging)
            return
        var p = canvas.mapFromItem(null, sx, sy)
        dragPoint = Qt.point(p.x, p.y)
        autoDx = p.x < 40 ? 12 : (p.x > treeW - 40 ? -12 : 0)
        autoDy = p.y < 40 ? 12 : (p.y > height - 40 ? -12 : 0)
        retarget()
    }

    function retarget() {
        if (!dragPoint)
            return
        dropPath = backend.dropAt((dragPoint.x - world.x) / world.scale, (dragPoint.y - world.y) / world.scale)
    }

    function finishDrag() {
        if (!dragging)
            return
        retarget()
        var target = dropPath
        var keys = dragKeys
        dragging = false
        autoDx = 0
        autoDy = 0
        backend.dropAt(-1e9, -1e9)
        dropPath = ""
        dragPoint = null
        dragKeys = []
        if (target)
            Ui.app.openMoveDialog(keys, target)
    }

    Timer {
        id: autoPan
        interval: 16
        repeat: true
        running: canvas.dragging && (canvas.autoDx !== 0 || canvas.autoDy !== 0)
        onTriggered: {
            canvas.panTo(world.x + canvas.autoDx, world.y + canvas.autoDy)
            canvas.retarget()
        }
    }

    // input --------------------------------------------------------------
    HoverHandler { id: hover }

    PinchHandler {
        id: pinch
        target: null
        property real startScale: 1
        onActiveChanged: {
            if (active)
                startScale = world.scale
            else
                canvas.saveView()
        }
        onActiveScaleChanged: {
            var c = centroid.position
            canvas.zoomAt(c.x, c.y, canvas.clamp(startScale * activeScale, 0.3, 2.5) / world.scale)
        }
    }

    MouseArea {
        id: ground
        anchors.fill: parent
        property real pressX: 0
        property real pressY: 0
        property real startX: 0
        property real startY: 0
        onPressed: mouse => {
            pressX = mouse.x
            pressY = mouse.y
            startX = world.x
            startY = world.y
            Ui.focusCanvas()
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return
            if (!canvas.panning && Math.abs(mouse.x - pressX) + Math.abs(mouse.y - pressY) > 3) {
                canvas.panning = true
                glide.stop()
            }
            if (canvas.panning)
                canvas.panTo(startX + mouse.x - pressX, startY + mouse.y - pressY)
        }
        onReleased: {
            if (canvas.panning) {
                canvas.panning = false
                canvas.saveView()
            }
        }
        onCanceled: canvas.panning = false
        onClicked: {
            if (!Ui.recentChildTap())
                backend.clearSelection()
        }
        onDoubleClicked: mouse => {
            if (!Ui.recentChildTap())
                canvas.fit(false, mouse.x, mouse.y)
        }
        onWheel: wheel => canvas.wheelPan(wheel)
    }

    // part: the share of the step left over after a card scrolled to its end
    function wheelPan(wheel, part) {
        if (part === undefined)
            part = 1
        if (wheel.modifiers & Qt.ControlModifier) {
            zoomAt(wheel.x, wheel.y, Math.pow(1.0018, wheel.angleDelta.y))
            return
        }
        var shift = (wheel.modifiers & Qt.ShiftModifier) !== 0
        var touch = wheel.phase !== Qt.NoScrollPhase && (wheel.pixelDelta.x !== 0 || wheel.pixelDelta.y !== 0)
        var dx, dy
        if (touch) {
            dx = wheel.pixelDelta.x
            dy = wheel.pixelDelta.y
        } else {
            dx = wheel.angleDelta.x / 120 * 140
            dy = wheel.angleDelta.y / 120 * 140
        }
        if (shift) {
            dx = dx + dy
            dy = 0
        }
        dx *= part
        dy *= part
        if (touch)
            panBy(dx, dy)
        else
            glideBy(dx, dy)
        saveTimer.restart()
    }

    Item {
        // tree content never slides under the side panels
        width: Math.max(0, canvas.treeW)
        height: canvas.height
        clip: true

        Item {
            id: world
            objectName: "world"
            transformOrigin: Item.TopLeft
            x: canvas.view && canvas.view.saved ? canvas.view.x : 30
            y: canvas.view && canvas.view.saved ? canvas.view.y : 24
            scale: canvas.view && canvas.view.saved ? canvas.view.scale : 1
            Component.onCompleted: {
                // detach from the settings once the start position is known
                x = x
                y = y
                scale = scale
            }
            Repeater {
                model: backend.nodeModel
                delegate: TreeNode {}
            }
        }
    }

    Rectangle {
        // drag ghost
        visible: canvas.dragging && canvas.dragPoint !== null
        x: canvas.dragPoint ? canvas.dragPoint.x + 14 : 0
        y: canvas.dragPoint ? canvas.dragPoint.y + 8 : 0
        z: 50
        height: 26
        width: ghostText.implicitWidth + 20
        radius: 7
        color: Theme.selection
        opacity: 0.95
        border.color: canvas.dropPath ? Theme.accent : Theme.line
        Text {
            id: ghostText
            anchors.centerIn: parent
            text: canvas.dragLabel + (canvas.dropPath ? "  →  " + backend.tildify(canvas.dropPath) : "")
            textFormat: Text.PlainText
            font.pixelSize: 12
            color: Theme.text
        }
    }

    Text {
        x: 10
        y: 10
        z: 5
        text: backend.stats + (backend.selectedCount > 1 ? "   ·   " + backend.selectedCount + " selected" : "")
        textFormat: Text.PlainText
        font.pixelSize: 12
        color: Theme.dim
    }

    HButton {
        objectName: "updateTree"
        z: 5
        visible: backend.layoutPending
        x: canvas.treeW - width - 16
        y: 6
        text: "Update tree"
        tip: "New layout available. Update and fit now, or move the pointer away to apply it."
        onClicked: canvas.fit(true)
    }

    PanBar {
        id: vbar
        objectName: "vbar"
        z: 6
        vertical: true
        x: canvas.treeW - 10
        width: 10
        height: canvas.height - 12
        pos: world.y
        minPos: canvas.minY
        maxPos: canvas.maxY
        viewLength: canvas.height
        onMoved: to => { canvas.panTo(world.x, to); saveTimer.restart() }
    }
    PanBar {
        id: hbar
        objectName: "hbar"
        z: 6
        vertical: false
        y: canvas.height - 10
        height: 10
        width: canvas.treeW - 12
        pos: world.x
        minPos: canvas.minX
        maxPos: canvas.maxX
        viewLength: canvas.treeW
        onMoved: to => { canvas.panTo(to, world.y); saveTimer.restart() }
    }
}
