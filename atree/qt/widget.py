"""Desktop-widget helpers: keep Desktop Icons NG clickable through our window (ui.md §14.2)."""
import math
import os
import subprocess
import threading

from PySide6.QtCore import QObject, QRect, QStandardPaths, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QRegion

SCHEMA = "org.gnome.shell.extensions.ding"
POLL_EVERY = 15000
SLACK = 16
CELLS = {"tiny": (70, 80), "small": (90, 90), "standard": (120, 106), "large": (130, 138)}


def gsetting(key, schema=SCHEMA):
    """A GSettings value as text with quotes stripped; any failure reads as ""."""
    try:
        out = subprocess.run(["gsettings", "get", schema, key], capture_output=True,
                             text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode:
        return ""
    return out.stdout.strip().strip("'")


def platform_desktop():
    return QStandardPaths.writableLocation(QStandardPaths.DesktopLocation)


def desktop_dir():
    return os.environ.get("XDG_DESKTOP_DIR") or platform_desktop()


def desktop_files():
    try:
        folder = desktop_dir()
        return [os.path.join(folder, n) for n in sorted(os.listdir(folder)) if not n.startswith(".")]
    except (OSError, TypeError):
        return []


def desktop_entries():
    return len(desktop_files())


def icon_positions(paths):
    """Screen positions of icons the user placed by hand (DING keeps them in GIO metadata)."""
    if not paths:
        return ()
    try:
        out = subprocess.run(["gio", "info", "-a", "metadata::nautilus-icon-position", *paths],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return ()
    found = []
    for line in out.splitlines():
        key, _, value = line.strip().partition(": ")
        if key == "metadata::nautilus-icon-position":
            try:
                x, y = (int(v) for v in value.split(","))
            except ValueError:
                continue
            found.append((x, y))
    return tuple(sorted(found))


def ding_state():
    """(start corner, icon size, icon count, placed icon positions) or None without icons."""
    corner = gsetting("start-corner")
    if not corner:
        return None
    size = gsetting("icon-size")
    files = desktop_files()
    count = (gsetting("show-home") == "true") + (gsetting("show-trash") == "true") + len(files)
    if not count:
        return None
    placed = () if gsetting("keep-arranged") == "true" else icon_positions(files)
    return corner, size, count, placed


def window_origin(window, dock):
    """Where the window sits on screen: Wayland hides it, so infer it from the dock and panel."""
    screen = window.screen()
    if screen is None:
        return 0, 0
    geo = screen.geometry()
    spare_w = max(0, geo.width() - window.width())
    spare_h = max(0, geo.height() - window.height())
    x = spare_w if dock == "LEFT" else 0
    y = 32 if dock == "BOTTOM" and spare_h > 32 else spare_h if dock != "BOTTOM" else 0
    return geo.x() + x, geo.y() + y


def icon_rects(state, width, height, origin=(0, 0)):
    """Rectangles (x, y, w, h) in window coordinates that DING's icons occupy."""
    if not state:
        return []
    placed = state[3] if len(state) > 3 else ()
    # icons the user placed by hand are cut out where they are; DING stacks the rest
    # (home, trash, new files) from its start corner
    stacked = state[2] - len(placed)
    hole = icon_hole(state[:2] + (stacked,), width, height) if stacked > 0 else None
    rects = [hole] if hole else []
    cw, ch = CELLS.get(state[1], CELLS["standard"])
    for x, y in placed:
        rects.append((x - origin[0] - SLACK // 2, y - origin[1] - SLACK // 2, cw + SLACK, ch + SLACK))
    return rects


def icon_hole(state, width, height):
    """The block (x, y, w, h) DING fills from its start corner in a window of that size."""
    if not state:
        return None
    corner, size, count = state[:3]
    cw, ch = CELLS.get(size, CELLS["standard"])
    cw, ch = cw + SLACK, ch + SLACK
    rows = max(1, int(height // ch))
    cols = math.ceil(count / rows)
    w, h = cols * cw, min(count, rows) * ch
    x = width - w if corner.endswith("right") else 0
    y = height - h if corner.startswith("bottom") else 0
    return x, y, w, h


class IconHole(QObject):
    """Masks the window's input region around DING's icon block."""
    _polled = Signal(object)

    def __init__(self, window, poll=ding_state):
        super().__init__(window)
        self.window = window
        self.poll_fn = poll
        self.state = None
        self.dock = ""
        self.mask = None
        self._busy = False
        # no mask or insets until the first poll has answered, as before the logo toggle: the
        # widget went invisible on Wayland once they were set while the window was still mapping
        self._ready = False
        self._polled.connect(self._on_poll, Qt.QueuedConnection)
        window.widthChanged.connect(self.apply)
        window.heightChanged.connect(self.apply)
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_EVERY)
        self._timer.timeout.connect(self.poll)
        self._timer.start()
        # Main.qml calls refresh() when the logo toggle changes; the first poll applies even when it
        # finds nothing new, so a toggle saved on takes effect at start
        window.setProperty("maskHelper", self)
        self.poll()

    @Slot()
    def refresh(self):
        self.apply()

    def poll(self):
        if self._busy:
            return
        self._busy = True
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self):
        try:
            state = self.poll_fn()
            dock = gsetting("dock-position", "org.gnome.shell.extensions.dash-to-dock")
            result = ("ok", (state, dock))
        except Exception:
            result = ("failed", None)
        try:
            self._polled.emit(result)
        except RuntimeError:
            pass  # the window is gone

    def _on_poll(self, result):
        self._busy = False
        first = not self._ready
        self._ready = True
        status, values = result
        if status == "ok":
            state, dock = values
            if (state, dock) != (self.state, self.dock):
                self.state, self.dock = state, dock
                self.apply()
                return
        if first:
            self.apply()

    def region(self):
        # logo toggle on: only the logo keeps input, the rest goes to the desktop below
        if self.window.property("desktopPass"):
            logo = self.window.property("passRect")
            if logo is not None and not logo.isEmpty():
                return QRegion(logo.toAlignedRect())
        rects = self.rects()
        if not rects:
            return QRegion()
        w, h = self.window.width(), self.window.height()
        region = QRegion(0, 0, w, h)
        for r in rects:
            region = region.subtracted(QRegion(QRect(*r)))
        return region

    def rects(self):
        w, h = self.window.width(), self.window.height()
        origin = window_origin(self.window, self.dock) if self.state and len(self.state) > 3 and self.state[3] else (0, 0)
        return [r for r in icon_rects(self.state, w, h, origin)
                if r[0] < w and r[1] < h and r[0] + r[2] > 0 and r[1] + r[3] > 0]

    def insets(self):
        """(left, right, top, right top) of the icon area, for keeping atree's controls out of
        it: the widths taken at each side, the highest icon, and the highest right-side icon."""
        w, h = self.window.width(), self.window.height()
        rects = self.rects()
        if not rects:
            return 0, 0, h, h
        left = max((x + rw for x, _y, rw, _rh in rects if x <= w / 2), default=0)
        right_side = [r for r in rects if r[0] > w / 2]
        right = max((w - x for x, _y, _rw, _rh in right_side), default=0)
        top = min(y for _x, y, _rw, _rh in rects)
        right_top = min((y for _x, y, _rw, _rh in right_side), default=h)
        return max(0, left), max(0, right), max(0, top), max(0, right_top)

    def apply(self, *_):
        if not self._ready:
            return
        left, right, top, right_top = self.insets()
        for name, value in (("holeLeft", left), ("holeRight", right), ("holeTop", top),
                            ("holeRightTop", right_top)):
            self.window.setProperty(name, value)
        region = self.region()
        if self.mask is not None and region == self.mask:
            return
        self.mask = region
        self.window.setMask(region)
