"""The shared task notepad (``tasks.json``), edited under an inter-process lock."""
import json
import os
import time

from atree import ops
from atree.fsutil import atomic_write


class _Unreadable(Exception):
    """The task file exists but could not be read; writing now would lose its tasks."""


def _clean(raw):
    if not isinstance(raw, list):
        raise ValueError("the task file does not hold a list")
    return [dict(t) for t in raw if isinstance(t, dict) and "id" in t]


def _collapse(text):
    return " ".join(str(text or "").split())


class TaskList:
    """In-memory copy of the task file.

    ``on_change()`` is called when the list visible to the view changed;
    ``on_error(message)`` when a save failed.

    A task file that exists but can't be read (I/O error, bad JSON) keeps the last good list
    and blocks edits, so a save never replaces tasks this window could not see.
    """

    def __init__(self, on_change=None, on_error=None):
        self.items = []
        self._mtime = None
        self.on_change = on_change or (lambda: None)
        self.on_error = on_error or (lambda msg: None)
        try:
            self._read_if_changed()
        except _Unreadable:
            pass

    @staticmethod
    def path():
        return ops.TASKS_FILE

    def _stamp(self):
        try:
            return os.stat(self.path()).st_mtime_ns
        except OSError:
            return None

    def _read_if_changed(self):
        """Reload when the file's mtime moved; True if the list changed.

        Raises ``_Unreadable`` (keeping the list as it was) when the file is there but unreadable.
        """
        stamp = self._stamp()
        if stamp == self._mtime and stamp is not None:
            return False
        try:
            with open(self.path(), encoding="utf-8") as f:
                items = _clean(json.load(f))
        except FileNotFoundError:
            items = []
        except (OSError, ValueError) as e:
            self._mtime = None  # try again next time
            raise _Unreadable(str(e)) from None
        self._mtime = stamp
        if items == self.items:
            return False
        self.items = items
        return True

    def reload(self):
        try:
            changed = self._read_if_changed()
        except _Unreadable:
            return  # reported when an edit is tried
        if changed:
            self.on_change()

    def _edit(self, fn):
        try:
            with ops.tasks_lock():
                try:
                    outside = self._read_if_changed()
                except _Unreadable as e:
                    self.on_error("can't read tasks, not saving: %s" % e)
                    return
                new = fn([dict(t) for t in self.items])
                if new is None or new == self.items:
                    if outside:
                        self.on_change()
                    return
                try:
                    atomic_write(self.path(), json.dumps(new, indent=1, ensure_ascii=False))
                except OSError as e:
                    self.on_error("can't save tasks: %s" % e)
                    if outside:
                        self.on_change()
                    return
                self.items = new
                self._mtime = self._stamp()
        except OSError as e:
            self.on_error("can't save tasks: %s" % e)
            return
        self.on_change()

    def add(self, text):
        text = _collapse(text)
        if not text:
            return
        self._edit(lambda items: items + [{"id": "%x" % time.time_ns(), "text": text, "done": False}])

    def edit(self, tid, text):
        text = _collapse(text)
        if not text:
            return
        self._edit(lambda items: [dict(t, text=text) if t["id"] == tid else t for t in items])

    def set_done(self, tid, done):
        self._edit(lambda items: [dict(t, done=bool(done)) if t["id"] == tid else t for t in items])

    def remove(self, tid):
        self._edit(lambda items: [t for t in items if t["id"] != tid])

    def clear_done(self):
        self._edit(lambda items: [t for t in items if not t.get("done")])

    def move(self, tid, to):
        def fn(items):
            src = next((i for i, t in enumerate(items) if t["id"] == tid), None)
            if src is None:
                return None
            to_i = max(0, min(int(to), len(items)))
            anchor = items[to_i]["id"] if to_i < len(items) else None
            task = items.pop(src)
            if anchor is None:
                at = len(items)
            elif anchor == tid:
                at = src
            else:
                at = next(i for i, t in enumerate(items) if t["id"] == anchor)
            items.insert(at, task)
            return items
        self._edit(fn)
