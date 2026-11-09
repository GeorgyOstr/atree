"""Helpers for the clock/calendar/status-line tests: a throwaway home and time-zone control."""
import os
import shutil
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from atree import calendars, statusline, timers  # noqa: E402


def set_tz(case, tz):
    """Set ``TZ`` for the C library and zoneinfo for the rest of the test."""
    old = os.environ.get("TZ")
    if tz is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = tz
    time.tzset()

    def restore():
        if old is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old
        time.tzset()
    case.addCleanup(restore)


class HomeCase(unittest.TestCase):
    """Every atree path redirected into a fresh temporary home."""

    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="atree-clock-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        self.data = os.path.join(self.home, "data", "atree")
        self.cache = os.path.join(self.home, "cache", "atree")
        self.claude = os.path.join(self.home, ".claude")
        patches = [
            (timers, "DATA", self.data), (timers, "FILE", os.path.join(self.data, "timers.json")),
            (timers, "LOCK", os.path.join(self.data, "timers.lock")),
            (calendars, "CACHE_DIR", self.cache), (calendars, "LIST", os.path.join(self.data, "calendars.json")),
            (calendars, "CACHE", os.path.join(self.cache, "calendar.json")),
            (statusline, "CACHE", self.cache), (statusline, "DATA", self.data),
            (statusline, "LIMITS", os.path.join(self.cache, "claude-limits.json")),
            (statusline, "NEXT", os.path.join(self.data, "statusline-next.json")),
            (statusline, "CLAUDE_DIR", self.claude),
            (statusline, "SETTINGS", os.path.join(self.claude, "settings.json")),
            (statusline, "BACKUP", os.path.join(self.claude, "settings.json.atree-backup")),
        ]
        for mod, name, value in patches:
            p = mock.patch.object(mod, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"HOME": self.home, "XDG_DATA_HOME": os.path.join(self.home, "data"),
                                           "XDG_CACHE_HOME": os.path.join(self.home, "cache"),
                                           "CLAUDE_CONFIG_DIR": self.claude})
        env.start()
        self.addCleanup(env.stop)
        self.notified = []
        p = mock.patch.object(timers, "notify", lambda *a, **k: self.notified.append(a))
        p.start()
        self.addCleanup(p.stop)

    def path(self, *parts):
        return os.path.join(self.home, *parts)


def write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    return path


def read(path, mode="rb"):
    with open(path, mode) as f:
        return f.read()
