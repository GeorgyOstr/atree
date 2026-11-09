"""Qt view models for the side column: timers and the calendar agenda, plus the wall-clock sleeper."""
import ctypes
import errno
import math
import os
import threading
import time
from datetime import date, datetime, timedelta

from PySide6.QtCore import (Property, QCoreApplication, QFileSystemWatcher, QObject, QSocketNotifier, Qt,
                            QTimer, Signal, Slot)

from .. import calendars, ical, timers
from ..calendars import PALETTE, FetchError

FETCH_EVERY = 600
REMIND = 300
FALLBACK_CAP = 300.0
MIN_DELAY = 0.05

_now = time.time


def _describe(e):
    return e.strerror or str(e)


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


_UNSEEN = object()


# ---------------------------------------------------------------- timerfd

TFD_TIMER_ABSTIME = 1
TFD_TIMER_CANCEL_ON_SET = 2
CLOCK_REALTIME = 0
NS_LIMIT = 2 ** 63


class _Timespec(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_nsec", ctypes.c_long)]


class _Itimerspec(ctypes.Structure):
    _fields_ = [("it_interval", _Timespec), ("it_value", _Timespec)]


_libc = None


def _lib():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
    return _libc


def _check(result):
    if result < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))
    return result


class TimerFD:
    """A CLOCK_REALTIME timerfd (os module on 3.13+, libc via ctypes otherwise)."""

    def __init__(self):
        flags = os.O_NONBLOCK | os.O_CLOEXEC
        if hasattr(os, "timerfd_create"):
            self.fd = os.timerfd_create(time.CLOCK_REALTIME, flags=flags)
        else:
            self.fd = _check(_lib().timerfd_create(CLOCK_REALTIME, flags))

    def set(self, ns, absolute):
        if not 0 <= ns < NS_LIMIT:
            raise OverflowError("deadline out of range")
        flags = TFD_TIMER_ABSTIME | TFD_TIMER_CANCEL_ON_SET if absolute else 0
        if hasattr(os, "timerfd_settime_ns"):
            os.timerfd_settime_ns(self.fd, flags=flags, initial=ns, interval=0)
            return
        spec = _Itimerspec()
        spec.it_value.tv_sec, spec.it_value.tv_nsec = divmod(ns, 10 ** 9)
        _check(_lib().timerfd_settime(self.fd, flags, ctypes.byref(spec), None))

    def get(self):
        """(seconds until expiry, interval seconds)."""
        if hasattr(os, "timerfd_gettime"):
            return os.timerfd_gettime(self.fd)
        spec = _Itimerspec()
        _check(_lib().timerfd_gettime(self.fd, ctypes.byref(spec)))
        value, interval = spec.it_value, spec.it_interval
        return value.tv_sec + value.tv_nsec / 1e9, interval.tv_sec + interval.tv_nsec / 1e9

    def read(self):
        return os.read(self.fd, 8)

    def close(self):
        os.close(self.fd)


# ---------------------------------------------------------------- folder watcher

_watcher = None


def folder_watcher():
    """The process-wide watcher on the data dir (files there are replaced by rename)."""
    global _watcher
    os.makedirs(timers.DATA, exist_ok=True)
    if _watcher is None or _watcher.directories() != [timers.DATA]:
        if _watcher is not None:
            _watcher.deleteLater()
        _watcher = QFileSystemWatcher([timers.DATA], QCoreApplication.instance())
    return _watcher


# ---------------------------------------------------------------- sleeper

class Sleeper:
    """One single-shot wake-up at an absolute wall-clock time, robust to suspend and clock changes."""

    def __init__(self, owner, wake, reload=None, native=True):
        self._wake = wake
        self.timer = QTimer(owner)
        self.timer.setSingleShot(True)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(wake)
        self.tfd = self.notifier = None
        if native:
            try:
                self.tfd = TimerFD()
            except (AttributeError, OSError):
                self.tfd = None
        if self.tfd is not None:
            self.notifier = QSocketNotifier(self.tfd.fd, QSocketNotifier.Read, owner)
            self.notifier.setEnabled(False)
            self.notifier.activated.connect(self._ready)
        owner.destroyed.connect(self.close)
        if reload is not None:
            folder_watcher().directoryChanged.connect(reload)

    def _fallback(self, when):
        delay = min(max(when - _now(), MIN_DELAY), FALLBACK_CAP)
        self.timer.start(int(round(delay * 1000)))

    def at(self, when):
        """Wake once at epoch ``when``; None disarms."""
        self.timer.stop()
        if self.tfd is None:
            if when is not None:
                self._fallback(when)
            return
        self.notifier.setEnabled(False)
        try:
            if when is None:
                self.tfd.set(0, False)
            else:
                self._arm(max(_now() + MIN_DELAY, when))
        except OverflowError:
            self.tfd.set(0, False)
            self._fallback(when)
            return
        self.notifier.setEnabled(when is not None)

    def _arm(self, value):
        ns = round(value * 1e9)
        try:
            self.tfd.set(ns, True)
        except OSError as e:
            if e.errno != errno.ECANCELED:  # a clock change raced the re-arm; the value still applies
                raise

    def _ready(self, *_):
        if self.tfd is None:
            return
        try:
            self.tfd.read()
        except BlockingIOError:
            return
        except OSError as e:
            if e.errno != errno.ECANCELED:
                raise
        self.at(None)
        self._wake()

    def close(self, *_):
        tfd, self.tfd = self.tfd, None
        for stop in (lambda: self.timer.stop(), lambda: self.notifier and self.notifier.setEnabled(False)):
            try:
                stop()
            except RuntimeError:  # already deleted with the owner
                pass
        if tfd is not None:
            tfd.close()


# ---------------------------------------------------------------- timers

class Timers(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.timers = []
        self._rows = []
        self._seen = _UNSEEN
        self._sleeper = Sleeper(self, self.tick, self.reload)
        self.reload()

    def _get_items(self):
        return self._rows

    items = Property(list, _get_items, notify=changed)

    def reload(self, *_):
        m = _mtime(timers.FILE)
        if m != self._seen:
            self._seen = m
            self.timers = timers.load()
            self.tick()

    def tick(self, now=None):
        now = _now() if now is None else now
        if any(not t.get("fired") and t["due"] <= now for t in self.timers):
            try:
                mine, self.timers = timers.claim_due(now)
            except OSError:
                mine = []
            else:
                self._seen = _mtime(timers.FILE)
            for t in mine:
                kind = "alarm" if t.get("alarm") else "timer"
                timers.notify(t.get("label") or kind.capitalize(), "%s · %s" % (kind, timers.hhmm(t["due"])))
        rows, wakes = [], []
        for t in sorted(self.timers, key=lambda t: t["due"]):
            row, wake = self._row(t, now)
            rows.append(row)
            if wake is not None:
                wakes.append(wake)
        self._sleeper.at(min(wakes) + 0.02 if wakes else None)
        if rows != self._rows:
            self._rows = rows
            self.changed.emit()

    @staticmethod
    def _row(t, now):
        due, alarm, fired = t["due"], bool(t.get("alarm")), bool(t.get("fired"))
        left = due - now
        if fired:
            when, wake = "done", None
        elif left <= 0:
            when, wake = "can't save", now + 30
        elif alarm:
            when, wake = timers.hhmm(due), due
        elif left > 60:
            m = math.ceil(left / 60)
            when = "%dh %dm" % divmod(m, 60) if m >= 60 else "%dm" % m
            wake = due - (m - 1) * 60
        else:
            s = max(1, math.ceil(left))
            when, wake = "%ds" % s, due - (s - 1)
        return {"id": t["id"], "label": t.get("label", ""), "when": when, "alarm": alarm, "fired": fired,
                "soon": not alarm and 0 < left <= 60}, wake

    @Slot(str, result=str)
    def add(self, text):
        parsed = timers.parse(text, _now())
        if parsed is None:
            return "try 25m, 1h30, 90s or 14:30"
        timer = {"id": timers.new_id(), "due": parsed["due"], "label": parsed["label"],
                 "alarm": parsed["alarm"], "fired": False}
        try:
            self.timers = timers.edit(lambda ts: ts.append(timer))
        except OSError as e:
            return "can't save timers: %s" % e
        self._seen = _mtime(timers.FILE)
        self.tick()
        return ""

    @Slot(str)
    def remove(self, timer_id):
        try:
            self.timers = timers.edit(lambda ts: [t for t in ts if t["id"] != timer_id])
            self._seen = _mtime(timers.FILE)
        except OSError:
            pass
        self.tick()

    def shutdown(self):
        self._sleeper.close()


# ---------------------------------------------------------------- agenda

class Agenda(QObject):
    changed = Signal()
    _round_done = Signal(object)
    _add_done = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cals = []
        self._cache = calendars.load_cache()
        self._fails = {}
        self._deadline = {}
        self._running = False
        self._closed = False
        self._state = {"events": [], "calendars": [], "adding": False, "error": ""}
        self._handled = set()
        self._seen = _UNSEEN
        self._round_done.connect(self._on_round)
        self._add_done.connect(self._on_added)
        self._sleeper = Sleeper(self, self.tick, self.reload)
        self.reload()

    events = Property(list, lambda self: self._state["events"], notify=changed)
    calendars = Property(list, lambda self: self._state["calendars"], notify=changed)
    adding = Property(bool, lambda self: self._state["adding"], notify=changed)
    error = Property(str, lambda self: self._state["error"], notify=changed)

    def _set(self, **values):
        if any(self._state[k] != v for k, v in values.items()):
            self._state.update(values)
            self.changed.emit()

    def _spawn(self, job):
        threading.Thread(target=job, daemon=True).start()

    def _deliver(self, signal, value):
        try:
            signal.emit(value)
        except RuntimeError:  # the agenda is gone
            pass

    # -------------------------------------------------------- reload / tick

    def reload(self, *_, force=False):
        m = _mtime(calendars.LIST)
        if m == self._seen and not force:
            return
        self._seen = m
        self._cals = calendars.load_list()
        ids = {c["id"] for c in self._cals}
        for d in (self._deadline, self._fails):
            for cid in [k for k in d if k not in ids]:
                del d[cid]
        for c in self._cals:
            if c["id"] not in self._deadline:
                self._deadline[c["id"]] = self._cache.get(c["id"], {}).get("at", 0) + FETCH_EVERY
        self.tick()

    def tick(self, now=None):
        now = _now() if now is None else now
        due = [cid for cid, d in self._deadline.items() if d <= now]
        if due and not self._running:
            self._start_round(due)
        today = datetime.fromtimestamp(now).date()
        tomorrow = today + timedelta(days=1)
        midnight = datetime.combine(tomorrow, datetime.min.time()).timestamp()
        end2 = datetime.combine(today + timedelta(days=2), datetime.min.time()).timestamp()
        groups = ([], [])
        wakes = [midnight]
        for cal in self._cals:
            cid, color = cal["id"], cal.get("color") or PALETTE[0]
            for ev in self._cache.get(cid, {}).get("events", []):
                if ev["allday"]:
                    s, e = date.fromisoformat(ev["start"]), date.fromisoformat(ev["end"])
                    for i, d in enumerate((today, tomorrow)):
                        if s <= d < e:
                            groups[i].append({"head": False, "title": ev["title"], "color": color,
                                              "uid": "%s/%s/%s/%d" % (cid, ev["uid"], ev["start"], i),
                                              "time": "all day", "now": False, "sort": 0})
                    continue
                s, e = ev["start"], ev["end"]
                expires = s + 60 if s == e else e
                if expires <= now or s >= end2:
                    continue
                live = s <= now < e
                groups[0 if s < midnight else 1].append({
                    "head": False, "title": ev["title"], "color": color, "uid": "%s/%s/%s" % (cid, ev["uid"], s),
                    "now": live, "time": timers.hhmm(s),
                    "until": "until %s" % timers.hhmm(e) if live and e > s else "", "sort": s})
                wakes.extend(x for x in (s - REMIND, s, expires) if x > now)
                if 0 < s - now <= REMIND and not self._remind(cid, ev, now):
                    retry = min(now + 30, s - 1)
                    if retry > now:
                        wakes.append(retry)
        rows = []
        for name, group in zip(("Today", "Tomorrow"), groups):
            if group:
                rows.append({"uid": name, "head": True, "title": name, "now": False})
                rows.extend(sorted(group, key=lambda r: (r["sort"], r["title"])))
        calrows = [{"id": c["id"], "name": c.get("name", ""), "color": c.get("color") or PALETTE[0],
                    "failing": self._fails.get(c["id"], 0) > 0} for c in self._cals]
        if not self._running:
            wakes.extend(self._deadline.values())
        self._sleeper.at(min(wakes) if self._cals else None)
        self._set(events=rows, calendars=calrows)

    def _remind(self, cid, ev, now):
        """Notify once across processes; False when the claim couldn't be recorded."""
        s = ev["start"]
        key = "event:%s:%s:%r" % (cid, ev["uid"], float(s))
        if key in self._handled:
            return True
        try:
            won = timers.claim_once(key)
        except OSError as e:
            self._set(error="can't save reminders: %s" % _describe(e))
            return False
        self._handled.add(key)
        if won:
            try:
                timers.notify(ev["title"], "in %d min · %s" % (max(1, round((s - now) / 60)), timers.hhmm(s)))
            except Exception:
                pass
        return True

    # -------------------------------------------------------- fetch rounds

    def _start_round(self, ids):
        self._running = True
        jobs = [(c["id"], c["url"]) for c in self._cals if c["id"] in ids]
        self._spawn(lambda: self._deliver(self._round_done, self._fetch_round(jobs)))

    @staticmethod
    def _fetch_round(jobs):
        disk = calendars.load_cache()
        results = {}
        for cid, url in jobs:
            now = _now()
            entry = disk.get(cid)
            if entry is not None and now - entry.get("at", 0) < FETCH_EVERY - 30:
                results[cid] = entry
                continue
            try:
                results[cid] = {"at": now, "events": ical.instances(calendars.fetch(url))}
            except FetchError as e:
                results[cid] = e
            except Exception as e:
                results[cid] = FetchError("can't read that calendar (%s)" % type(e).__name__)
        return results

    def _on_round(self, results, now=None):
        if self._closed:
            return
        now = _now() if now is None else now
        self._running = False
        ids = {c["id"] for c in self._cals}
        for cid, result in results.items():
            if cid not in ids:
                continue
            if isinstance(result, Exception):
                n = self._fails[cid] = self._fails.get(cid, 0) + 1
                self._deadline[cid] = now + min(3600, 60 * 2 ** (n - 1))
            else:
                self._fails.pop(cid, None)
                self._cache[cid] = result
                self._deadline[cid] = result["at"] + FETCH_EVERY
        self._save_cache()
        self.tick(now)

    def _save_cache(self, report=True):
        disk = calendars.load_cache()
        merged = {}
        for c in self._cals:
            entries = [x for x in (self._cache.get(c["id"]), disk.get(c["id"])) if x is not None]
            if entries:
                merged[c["id"]] = max(entries, key=lambda x: x.get("at", 0))
        self._cache = merged
        try:
            calendars.save_cache(merged)
        except OSError as e:
            if report:
                self._set(error="can't save calendar cache: %s" % _describe(e))

    # -------------------------------------------------------- adding / removing

    @Slot(str, str)
    def addCalendar(self, url, name):
        url, name = url.strip(), " ".join(name.split())
        if not url or self._state["adding"]:
            return
        if any(c["url"] == url for c in self._cals):
            self._set(error="that calendar is already added")
            return
        self._set(adding=True, error="")
        self._spawn(lambda: self._deliver(self._add_done, self._fetch_new(url, name)))

    @staticmethod
    def _fetch_new(url, name):
        try:
            text = calendars.fetch(url)
            events = ical.instances(text)
            return name or ical.calname(text) or "Calendar", url, {"at": _now(), "events": events}
        except FetchError as e:
            return e
        except Exception as e:
            return FetchError("can't read that calendar (%s)" % type(e).__name__)

    def _on_added(self, result):
        if self._closed:
            return
        if isinstance(result, Exception):
            self._set(adding=False, error=str(result))
            return
        name, url, entry = result
        cid = timers.new_id()
        self._cache[cid] = entry
        try:
            with timers.lock():
                cals = calendars.load_list()
                cals.append({"id": cid, "name": name, "url": url, "color": calendars.pick_color(cals)})
                calendars.save_list(cals)
        except OSError as e:
            del self._cache[cid]
            self._set(adding=False, error="can't save calendars: %s" % _describe(e))
            return
        self.reload(force=True)
        self._save_cache(report=False)
        self._set(adding=False, error="")

    @Slot(str)
    def removeCalendar(self, cal_id):
        try:
            with timers.lock():
                calendars.save_list([c for c in calendars.load_list() if c["id"] != cal_id])
        except OSError as e:
            self._set(error="can't save calendars: %s" % _describe(e))
        self.reload(force=True)
        self._cache.pop(cal_id, None)
        self._save_cache()
        self.tick()

    def shutdown(self):
        self._closed = True
        self._sleeper.close()
