"""Shared fixtures for the UI tests: an isolated HOME, offscreen Qt, desktop stubs."""
import os
import stat
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = tempfile.mkdtemp(prefix="atree-ui-")
HOME = os.path.join(ROOT, "home")
STUBS = os.path.join(ROOT, "bin")
CALLS = os.path.join(ROOT, "calls.log")


def _setup_env():
    os.makedirs(HOME)
    os.makedirs(STUBS)
    env = {
        "HOME": HOME,
        "XDG_DATA_HOME": os.path.join(HOME, ".local", "share"),
        "XDG_CONFIG_HOME": os.path.join(HOME, ".config"),
        "XDG_CACHE_HOME": os.path.join(HOME, ".cache"),
        "XDG_STATE_HOME": os.path.join(HOME, ".local", "state"),
        "XDG_RUNTIME_DIR": os.path.join(ROOT, "run"),
        "CLAUDE_CONFIG_DIR": os.path.join(HOME, ".claude"),
        "CODEX_HOME": os.path.join(HOME, ".codex"),
        "COPILOT_HOME": os.path.join(HOME, ".copilot"),
        "GEMINI_CLI_HOME": HOME,
        "OPENCODE_DB": os.path.join(HOME, "data", "opencode", "opencode.db"),
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QUICK_BACKEND": "software",
        "QT_QUICK_CONTROLS_STYLE": "Basic",
        "SHELL": "/bin/sh",
        "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
        "no_proxy": "127.0.0.1,localhost",
        "PATH": STUBS + os.pathsep + os.environ.get("PATH", ""),
    }
    for k in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        os.makedirs(env[k], exist_ok=True)
    os.makedirs(env["XDG_RUNTIME_DIR"], mode=0o700)
    os.environ.update(env)
    os.environ.pop("XDG_DESKTOP_DIR", None)
    for name in ("gsettings", "xdg-open", "notify-send", "x-terminal-emulator", "gnome-terminal",
                 "ptyxis", "terminator", "paplay", "canberra-gtk-play", "claude", "codex", "gemini",
                 "opencode", "copilot", "gnome-extensions", "curl", "wget"):
        path = os.path.join(STUBS, name)
        with open(path, "w") as f:
            f.write('#!/bin/sh\necho "%s $*" >> "%s"\nexit 1\n' % (name, CALLS))
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)


_setup_env()

from PySide6.QtCore import (QCoreApplication, QEventLoop, QObject, QPointF, QTimer, Property,  # noqa: E402
                            Qt, Signal, Slot, qInstallMessageHandler, QtMsgType)
from PySide6.QtGui import QGuiApplication  # noqa: E402

_app = QGuiApplication.instance() or QGuiApplication([sys.argv[0]])
_app.setOrganizationName("atree-test")
_app.setApplicationName("atree")

from atree import ops  # noqa: E402

ops.DATA = os.path.join(os.environ["XDG_DATA_HOME"], "atree")
ops.ARCHIVE_DIR = os.path.join(ops.DATA, "archive")
ops.ARCHIVE_INDEX = os.path.join(ops.ARCHIVE_DIR, "index.json")
ops.TASKS_FILE = os.path.join(ops.DATA, "tasks.json")

MESSAGES = []


def _handler(kind, context, message):
    if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        MESSAGES.append(message)


qInstallMessageHandler(_handler)


def app():
    return _app


def calls():
    try:
        with open(CALLS) as f:
            return f.read().splitlines()
    except OSError:
        return []


def pump(ms=0):
    """Run the event loop for ``ms`` milliseconds (at least one pass)."""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()
    _app.processEvents()


NOW = 1_790_000_000.0


class Clock:
    def __init__(self, now=NOW, mono=1000.0):
        self.now = now
        self.mono_t = mono

    def wall(self):
        return self.now

    def mono(self):
        return self.mono_t

    def advance(self, seconds):
        self.now += seconds
        self.mono_t += seconds


def talk(key, cwd, updated=0.0, live=False, tool="claude", **kw):
    t = {"key": "%s:%s" % (tool, key), "id": key, "tool": tool, "title": kw.pop("title", key),
         "first_msg": kw.pop("first_msg", "prompt of " + key), "cwd": cwd, "branch": None,
         "created": kw.pop("created", updated - 100), "updated": updated, "live": live,
         "live_status": "busy" if live else None, "prompts": 2, "context": 1000, "tokens": 5000,
         "agents": 0, "related": [], "parent": None, "size": 2_000_000, "path": "/x/" + key,
         "days": {}, "archived": False}
    t.update(kw)
    return t


def make_backend(clock=None, settings_path=None):
    from atree.qt.backend import Backend
    if os.path.exists(ops.TASKS_FILE):
        os.unlink(ops.TASKS_FILE)
    b = Backend(autostart=False, settings_path=settings_path)
    clock = clock or Clock()
    b.clock = clock.wall
    b.mono = clock.mono
    b.home = HOME
    b._test_clock = clock
    return b


class FakeTimers(QObject):
    changed = Signal()

    def __init__(self, items=None):
        super().__init__()
        self._items = items or []
        self.added = []
        self.removed = []

    items = Property("QVariantList", lambda self: self._items, notify=changed)

    @Slot(str, result=str)
    def add(self, text):
        self.added.append(text)
        return "" if text.strip() else "try 25m, 1h30, 90s or 14:30"

    @Slot(str)
    def remove(self, tid):
        self.removed.append(tid)

    def shutdown(self):
        pass


class FakeAgenda(QObject):
    changed = Signal()

    def __init__(self, events=None, calendars=None):
        super().__init__()
        self._events = events or []
        self._calendars = calendars or []
        self._adding = False
        self._error = ""
        self.added = []

    events = Property("QVariantList", lambda self: self._events, notify=changed)
    calendars = Property("QVariantList", lambda self: self._calendars, notify=changed)
    adding = Property(bool, lambda self: self._adding, notify=changed)
    error = Property(str, lambda self: self._error, notify=changed)

    @Slot(str, str)
    def addCalendar(self, url, name):
        self.added.append((url, name))

    @Slot(str)
    def removeCalendar(self, cid):
        pass

    def shutdown(self):
        pass


class View:
    """Main.qml loaded with a backend, plus helpers to find and poke items."""

    def __init__(self, backend, widget=False, timers=None, agenda=None, size=(1500, 950)):
        from atree.qt import app as app_mod
        self.backend = backend
        self.timers = timers or FakeTimers()
        self.agenda = agenda or FakeAgenda()
        del MESSAGES[:]
        from PySide6.QtCore import QSettings
        stored = QSettings(_app.organizationName(), _app.applicationName())
        stored.remove("view")
        stored.sync()
        self.warnings = []
        self.engine = app_mod.make_engine(backend, widget, self.timers, self.agenda)
        self.engine.warnings.connect(lambda ws: self.warnings.extend(w.toString() for w in ws))
        roots = app_mod.load(self.engine)
        assert roots, "Main.qml did not load: %s %s" % (self.warnings, MESSAGES)
        self.win = roots[0]
        self.win.resize(*size)
        self.win.show()
        pump(50)

    def all_warnings(self):
        return list(self.warnings) + [m for m in MESSAGES]

    def find(self, name):
        if self.win.objectName() == name:
            return self.win
        return self.win.findChild(QObject, name)

    def find_all(self, name):
        out = []
        stack = [self.win.contentItem()]
        while stack:
            it = stack.pop()
            if it.objectName() == name:
                out.append(it)
            stack.extend(it.childItems())
        return out

    def prop(self, name):
        alias = {"tree": "treeCanvas", "viewSettings": "viewSettings"}
        return self.find(alias.get(name, name))

    def close(self):
        from atree.qt import app as app_mod
        self.win.close()
        self.backend.shutdown()
        app_mod.destroy_engine(self.engine)
        pump(0)


# ---------------------------------------------------------------- QML interaction helpers

def call(obj, name, *args):
    from PySide6.QtCore import QMetaObject, Q_ARG, Q_RETURN_ARG
    try:
        return QMetaObject.invokeMethod(obj, name, Qt.DirectConnection, Q_RETURN_ARG("QVariant"),
                                        *[Q_ARG("QVariant", a) for a in args])
    except RuntimeError:  # a C++ slot without a return value
        return QMetaObject.invokeMethod(obj, name, Qt.DirectConnection)


def scene_rect(item):
    from PySide6.QtCore import QRectF
    p = item.mapToScene(QPointF(0, 0))
    return QRectF(p.x(), p.y(), item.width(), item.height())


def center(item):
    r = scene_rect(item)
    return r.center()


def mouse_move(win, x, y):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    QTest.mouseMove(win, QPoint(int(x), int(y)))
    pump(0)


def click(win, x, y, button=Qt.LeftButton):
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    QTest.mouseClick(win, button, Qt.NoModifier, QPoint(int(x), int(y)))
    pump(0)


def wheel(win, x, y, dy=-120, dx=0, pixel=None, modifiers=Qt.NoModifier, phase=None):
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QWheelEvent
    pos = QPointF(x, y)
    if pixel is not None:
        for ph, d in ((Qt.ScrollBegin, 0), (Qt.ScrollUpdate, int(pixel)), (Qt.ScrollEnd, 0)):
            ev = QWheelEvent(pos, win.mapToGlobal(pos), QPoint(0, d), QPoint(0, d),
                             Qt.NoButton, modifiers, ph, False)
            QCoreApplication.sendEvent(win, ev)
        pump(0)
        return
    else:
        ev = QWheelEvent(pos, win.mapToGlobal(pos), QPoint(0, 0), QPoint(int(dx), int(dy)),
                         Qt.NoButton, modifiers, phase or Qt.NoScrollPhase, False)
    QCoreApplication.sendEvent(win, ev)
    pump(0)


def walk(item):
    stack = [item]
    while stack:
        it = stack.pop()
        yield it
        stack.extend(it.childItems())


def qml_eval(item, expr):
    from PySide6.QtQml import QQmlEngine, QQmlExpression
    ctx = QQmlEngine.contextForObject(item)
    value, undefined = QQmlExpression(ctx, item, expr).evaluate()
    return value


def type_text(win, text):
    from PySide6.QtTest import QTest
    for ch in text:
        QTest.keyClick(win, ch)
    pump(0)
