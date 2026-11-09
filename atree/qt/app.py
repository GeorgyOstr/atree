"""GUI start-up: application, backend, QML engine (core.md §12.1, ui.md §2)."""
import os
import sys
import threading

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication, QIcon, QSurfaceFormat
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle

QML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")
MAIN_QML = os.path.join(QML_DIR, "Main.qml")
ICON = os.path.join(QML_DIR, "icon.svg")


class _Idle(QObject):
    """Stand-in for the timers / agenda objects when the clock module is unavailable."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)

    items = Property("QVariantList", lambda self: [], notify=changed)
    events = Property("QVariantList", lambda self: [], notify=changed)
    calendars = Property("QVariantList", lambda self: [], notify=changed)
    adding = Property(bool, lambda self: False, notify=changed)
    error = Property(str, lambda self: "", notify=changed)

    @Slot(str, result=str)
    def add(self, text):
        return "timers are unavailable"

    @Slot(str)
    def remove(self, tid):
        pass

    @Slot(str, str)
    def addCalendar(self, url, name):
        pass

    @Slot(str)
    def removeCalendar(self, cid):
        pass

    def shutdown(self):
        pass


def request_alpha():
    fmt = QSurfaceFormat.defaultFormat()
    fmt.setAlphaBufferSize(8)
    QSurfaceFormat.setDefaultFormat(fmt)
    QQuickWindow.setDefaultAlphaBuffer(True)


def make_backend():
    from atree.qt.backend import Backend
    return Backend()


def make_clock_objects():
    try:
        from atree.qt.clock import Agenda, Timers
        return Timers(), Agenda()
    except Exception as e:  # the panels still show, empty
        print("atree: timers and calendar unavailable: %s" % e, file=sys.stderr)
        return _Idle(), _Idle()


def make_engine(backend, widget, timers, agenda):
    global _styled
    if not _styled:
        QQuickStyle.setStyle("Basic")
        _styled = True
    engine = QQmlApplicationEngine()
    ctx = engine.rootContext()
    ctx.setContextProperty("backend", backend)
    ctx.setContextProperty("desktopWidget", bool(widget))
    ctx.setContextProperty("timers", timers)
    ctx.setContextProperty("agenda", agenda)
    return engine


def load(engine):
    engine.load(QUrl.fromLocalFile(MAIN_QML))
    return engine.rootObjects()


def destroy_engine(engine):
    import shiboken6
    if shiboken6.isValid(engine):
        shiboken6.delete(engine)


def keep_icons_clickable(window):
    from atree.qt.widget import IconHole
    return IconHole(window)


def run_loop(app):
    return app.exec()


_keep = []
_styled = False


def main(widget=False):
    if widget:
        request_alpha()
    # names first, then the application; Qt gets only the program name, never our options
    org = "atree-desktop" if widget else "atree"
    QGuiApplication.setOrganizationName(org)
    QGuiApplication.setApplicationName("atree")
    QGuiApplication.setDesktopFileName("atree")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    app.setWindowIcon(QIcon(ICON))
    from atree import ops
    threading.Thread(target=ops.adopt_shell_path, name="atree-shell-path", daemon=True).start()
    backend = make_backend()
    timers, agenda = make_clock_objects()
    engine = None
    try:
        engine = make_engine(backend, widget, timers, agenda)
        roots = load(engine)
        if not roots:
            return 1
        if widget:
            _keep.append(keep_icons_clickable(roots[0]))
        return run_loop(app)
    finally:
        try:
            backend.shutdown()
        finally:
            for obj in (timers, agenda):
                try:
                    obj.shutdown()
                except Exception:
                    pass
            if engine is not None:
                destroy_engine(engine)
