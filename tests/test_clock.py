"""Qt-side clock logic: sleeper, timers view model, agenda; plus calendar list/cache/fetch (spec clock.md §5, §8-10)."""
import errno
import http.client
import http.server
import json
import os
import socket
import stat
import threading
import time
import traceback
import unittest
import urllib.error
from datetime import datetime, timezone
from unittest import mock

from clocksupport import HomeCase, read, set_tz, write

from PySide6.QtCore import QCoreApplication, QElapsedTimer, QObject
from PySide6.QtGui import QGuiApplication

from atree import calendars, ical, timers
from atree.calendars import FetchError
from atree.qt import clock

app = QCoreApplication.instance() or QGuiApplication([])

SECRET = "https://calendar.example/private-abc123/basic.ics"


def spin(seconds, until=None):
    clock_ = QElapsedTimer()
    clock_.start()
    while clock_.elapsed() < seconds * 1000:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.002)
    return False


class FakeTFD:
    fd = -1

    def __init__(self):
        self.sets, self.read_error = [], None

    def set(self, ns, absolute):
        if not 0 <= ns < clock.NS_LIMIT:
            raise OverflowError
        self.sets.append((ns, absolute))

    def read(self):
        if self.read_error:
            raise self.read_error
        return b"\1" + b"\0" * 7

    def close(self):
        pass


# ---------------------------------------------------------------- sleeper

class SleeperCase(unittest.TestCase):
    def setUp(self):
        self.owner = QObject()
        self.wakes = []
        self.sleeper = clock.Sleeper(self.owner, lambda: self.wakes.append(time.time()))
        self.addCleanup(self.sleeper.close)

    def now(self, value):
        p = mock.patch.object(clock, "_now", lambda: value)
        p.start()
        self.addCleanup(p.stop)

    def fake(self):
        real = self.sleeper.tfd
        self.sleeper.tfd = FakeTFD()
        real.close()
        return self.sleeper.tfd

    def test_R_sleep_01_native_creation(self):
        tfd = self.sleeper.tfd
        self.assertIsNotNone(tfd)
        self.assertEqual(tuple(tfd.get()), (0, 0))
        self.assertFalse(self.sleeper.notifier.isEnabled())
        self.assertFalse(os.get_blocking(tfd.fd))
        self.assertFalse(os.get_inheritable(tfd.fd))

    def test_R_sleep_01_fallback_when_unavailable(self):
        for error in (OSError(errno.ENOSYS, "nope"), AttributeError("no api")):
            with mock.patch.object(clock, "TimerFD", side_effect=error):
                s = clock.Sleeper(QObject(self.owner), lambda: None)
            self.assertIsNone(s.tfd)
            self.assertIsNone(s.notifier)

    def test_R_sleep_02_arm_and_disarm(self):
        self.now(1000)
        tfd = self.fake()
        self.sleeper.at(1240)
        self.assertEqual(tfd.sets[-1], (1240 * 10 ** 9, True))
        self.assertTrue(self.sleeper.notifier.isEnabled())
        self.assertFalse(self.sleeper.timer.isActive())
        self.sleeper.at(900)
        self.assertEqual(tfd.sets[-1], (round(1000.05 * 1e9), True))
        self.sleeper.at(None)
        self.assertEqual(tfd.sets[-1], (0, False))
        self.assertFalse(self.sleeper.notifier.isEnabled())

    def test_R_sleep_02_ecanceled_is_success_other_errors_propagate(self):
        self.now(1000)
        tfd = self.fake()
        tfd.set = mock.Mock(side_effect=OSError(errno.ECANCELED, "canceled"))
        self.sleeper.at(1240)
        self.assertTrue(self.sleeper.notifier.isEnabled())
        tfd.set = mock.Mock(side_effect=OSError(errno.EINVAL, "bad"))
        with self.assertRaises(OSError):
            self.sleeper.at(1240)

    def test_R_sleep_02_unrepresentable_uses_fallback(self):
        self.sleeper.at(time.time() + 3600)
        far = datetime(3000, 1, 1, tzinfo=timezone.utc).timestamp()
        self.sleeper.at(far)
        self.assertEqual(tuple(self.sleeper.tfd.get()), (0, 0))
        self.assertFalse(self.sleeper.notifier.isEnabled())
        self.assertTrue(self.sleeper.timer.isActive())
        self.assertEqual(self.sleeper.timer.interval(), 300000)
        self.sleeper.at(None)
        self.assertFalse(self.sleeper.timer.isActive())

    def test_R_sleep_03_hour_ahead(self):
        self.sleeper.at(time.time() + 3600)
        value, interval = self.sleeper.tfd.get()
        self.assertTrue(3590 < value <= 3600)
        self.assertEqual(interval, 0)
        self.assertTrue(self.sleeper.notifier.isEnabled())
        self.assertFalse(self.sleeper.timer.isActive())

    def test_R_sleep_04_readiness(self):
        tfd = self.fake()
        self.sleeper.at(time.time() + 10)
        tfd.read_error = BlockingIOError(errno.EAGAIN, "again")
        self.sleeper._ready()
        self.assertEqual(self.wakes, [])
        self.assertTrue(self.sleeper.notifier.isEnabled())
        tfd.read_error = OSError(errno.ECANCELED, "clock set")
        self.sleeper._ready()
        self.assertEqual(len(self.wakes), 1)
        self.assertEqual(tfd.sets[-1], (0, False))
        tfd.read_error = OSError(errno.EIO, "io")
        with self.assertRaises(OSError):
            self.sleeper._ready()

    def test_R_sleep_05_single_shot_delivery(self):
        deadline = time.time() + 0.06
        self.sleeper.at(deadline)
        app.processEvents()
        self.assertEqual(self.wakes, [])
        self.assertTrue(spin(0.5, lambda: self.wakes))
        self.assertGreaterEqual(self.wakes[0], deadline)
        self.assertLess(self.wakes[0], deadline + 0.12)
        self.assertEqual(tuple(self.sleeper.tfd.get()), (0, 0))
        self.assertFalse(self.sleeper.notifier.isEnabled())
        spin(0.1)
        self.assertEqual(len(self.wakes), 1)
        self.sleeper.at(time.time() + 0.06)
        spin(0.3, lambda: len(self.wakes) > 1)
        self.assertEqual(len(self.wakes), 2)

    def test_R_sleep_06_past_deadline_and_cleared_expirations(self):
        self.sleeper.at(0)
        spin(0.3, lambda: self.wakes)
        self.assertEqual(len(self.wakes), 1)
        spin(0.05)
        self.assertEqual(len(self.wakes), 1)
        for rearm in (None, time.time() + 3600):
            self.sleeper.at(time.time() + 0.01)
            time.sleep(0.03)
            self.sleeper.at(rearm)
            spin(0.1)
            self.assertEqual(len(self.wakes), 1, rearm)

    def test_R_sleep_07_suspend_and_clock_change(self):
        tfd = self.fake()
        seen = []
        self.sleeper._wake = lambda: seen.append(clock._now())
        with mock.patch.object(clock, "_now", lambda: 1000):
            self.sleeper.at(1240)
        self.assertEqual(tfd.sets[-1], (1240 * 10 ** 9, True))
        with mock.patch.object(clock, "_now", lambda: 1600):
            self.sleeper._ready()
            tfd.read_error = BlockingIOError(errno.EAGAIN, "again")
            self.sleeper._ready()
        self.assertEqual(seen, [1600])
        self.assertFalse(self.sleeper.notifier.isEnabled())
        for now in (500, 1600):
            tfd.read_error = None
            with mock.patch.object(clock, "_now", lambda: now):
                self.sleeper.at(1240)
                tfd.read_error = OSError(errno.ECANCELED, "clock set")
                self.sleeper._ready()
            self.assertEqual(seen[-1], now)
            self.assertFalse(self.sleeper.notifier.isEnabled())

    def test_R_sleep_08_fallback_mode(self):
        s = clock.Sleeper(self.owner, lambda: self.wakes.append(1), native=False)
        self.assertIsNone(s.tfd)
        with mock.patch.object(clock, "_now", lambda: 1000):
            for when, ms in ((1240, 240000), (2000, 300000), (900, 50)):
                s.at(when)
                self.assertTrue(s.timer.isActive())
                self.assertEqual(s.timer.interval(), ms)
        self.assertTrue(spin(0.5, lambda: self.wakes))
        self.assertFalse(s.timer.isActive())
        spin(0.1)
        self.assertEqual(self.wakes, [1])
        s.at(time.time() + 0.01)
        s.at(None)
        spin(0.1)
        self.assertEqual(self.wakes, [1])

    def test_R_sleep_09_owner_destroyed(self):
        owner = QObject()
        wakes = []
        s = clock.Sleeper(owner, lambda: wakes.append(1))
        fd = s.tfd.fd
        closes = []
        real_close = s.tfd.close
        s.tfd.close = lambda: closes.append(1) or real_close()
        s.at(time.time() + 0.02)
        import shiboken6
        shiboken6.delete(owner)
        self.assertEqual(closes, [1])
        with self.assertRaises(OSError):
            os.fstat(fd)
        s.close()
        spin(0.1)
        self.assertEqual((closes, wakes), ([1], []))


# ---------------------------------------------------------------- view-model fixtures

class ViewCase(HomeCase):
    def setUp(self):
        super().setUp()
        set_tz(self, "UTC")
        self.clock = [1000.0]
        for target, name, value in ((clock, "_now", lambda: self.clock[0]),
                                    (clock.Sleeper, "at", lambda s, when: self.armed.append(when))):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.armed = []

    def counter(self, obj):
        hits = []
        obj.changed.connect(lambda: hits.append(1))
        return hits


class TimersView(ViewCase):
    def make(self):
        t = clock.Timers()
        self.addCleanup(t.shutdown)
        return t

    def store(self, *records):
        write(timers.FILE, json.dumps({"timers": list(records), "claimed": {}}))

    def test_R_tv_01_R_tv_10_startup_and_reload(self):
        write(timers.FILE, b"\xff")
        t = self.make()
        self.assertEqual(t.items, [])
        self.store({"id": "a", "due": 1600}, {"id": "", "due": 5}, None)
        t.reload()
        self.assertEqual([r["id"] for r in t.items], ["a"])
        with mock.patch.object(timers, "load", side_effect=AssertionError("reloaded")):
            t.reload()

    def test_R_tv_02_claim_and_notify(self):
        self.store({"id": "c", "due": 990}, {"id": "a", "due": 995, "alarm": True},
                   {"id": "l", "due": 999, "label": "tea"})
        t = self.make()
        self.assertEqual(sorted(self.notified), sorted([("Timer", "timer · 00:16"), ("Alarm", "alarm · 00:16"),
                                                        ("tea", "timer · 00:16")]))
        self.assertEqual([r["when"] for r in t.items], ["done"] * 3)
        self.assertEqual(t._seen, os.stat(timers.FILE).st_mtime_ns)
        self.assertIsNone(self.armed[-1])

    def test_R_tv_03_rows_and_wakes(self):
        self.store({"id": "f", "due": 10, "fired": True, "label": "x"}, {"id": "al", "due": 4600, "alarm": True},
                   {"id": "long", "due": 1000 + 3600 * 2 + 30}, {"id": "mid", "due": 1000 + 150},
                   {"id": "short", "due": 1000 + 42.5})
        t = self.make()
        rows = {r["id"]: r for r in t.items}
        self.assertEqual([r["id"] for r in t.items], ["f", "short", "mid", "al", "long"])
        self.assertEqual(rows["f"], {"id": "f", "label": "x", "when": "done", "alarm": False, "fired": True,
                                     "soon": False})
        self.assertEqual(rows["al"]["when"], "01:16")
        self.assertEqual(rows["long"]["when"], "2h 1m")
        self.assertEqual(rows["mid"]["when"], "3m")
        self.assertEqual((rows["short"]["when"], rows["short"]["soon"]), ("43s", True))
        self.assertAlmostEqual(self.armed[-1], 1000 + 42.5 - 42 + 0.02)

    def test_R_tv_04_claim_failure(self):
        for due, alarm in ((999, False), (1000, True)):
            self.store({"id": "d", "due": due, "alarm": alarm})
            calls = []
            with mock.patch.object(timers, "claim_due",
                                   lambda now: calls.append(now) or (_ for _ in ()).throw(OSError("disk full"))):
                t = self.make()
                hits = self.counter(t)
                for now in (1000, 1000, 1030.02, 1060.04):
                    self.clock[0] = now
                    t.tick()
                    self.assertAlmostEqual(self.armed[-1], now + 30.02)
            self.assertEqual(calls, [1000, 1000, 1000, 1030.02, 1060.04])
            self.assertEqual(t.items[0], {"id": "d", "label": "", "when": "can't save", "alarm": alarm,
                                          "fired": False, "soon": False})
            self.assertEqual(hits, [])
            self.assertFalse(timers.load()[0].get("fired"))
            self.assertEqual(self.notified, [])
            self.clock[0] = 1000

    def test_R_tv_05_claim_failure_with_other_timers(self):
        for other, armed, when in (({"id": "o", "due": 1005.25}, 1000.27, "6s"),
                                   ({"id": "o", "due": 1071.25}, 1011.27, "2m"),
                                   ({"id": "o", "due": 1012.5, "alarm": True}, 1012.52, timers.hhmm(1012.5))):
            self.clock[0] = 1000
            self.store({"id": "d", "due": 999}, other)
            with mock.patch.object(timers, "claim_due", side_effect=OSError("disk full")):
                t = self.make()
                self.assertAlmostEqual(self.armed[-1], armed)
                self.assertEqual(t.items[1]["when"], when)
                self.clock[0] = armed
                t.tick()
                self.assertGreater(self.armed[-1], armed)
                if other["due"] == 1005.25:
                    self.assertAlmostEqual(self.armed[-1], 1001.27)
                    self.assertEqual(t.items[1]["when"], "5s")

    def test_R_tv_06_two_views_recover(self):
        self.store({"id": "due", "due": 999, "label": "tea"})
        before = read(timers.FILE)
        attempts = []
        real_write = timers.atomic_write

        def failing(*a, **k):
            attempts.append(1)
            raise OSError(28, "disk full")
        with mock.patch.object(timers, "atomic_write", failing):
            views = [self.make(), self.make()]
        hits = [self.counter(v) for v in views]
        self.assertEqual(len(attempts), 2)
        self.assertEqual(read(timers.FILE), before)
        self.assertEqual(self.notified, [])
        self.assertAlmostEqual(self.armed[-1], 1030.02)
        claims = []
        real_claim = timers.claim_due
        with mock.patch.object(timers, "claim_due", lambda now: claims.append(now) or real_claim(now)), \
                mock.patch.object(timers, "atomic_write", real_write):
            self.clock[0] = 1030.02
            for v in views:
                v.tick()
            for v in views:
                self.assertEqual(v.items[0]["when"], "done")
                self.assertEqual(v._seen, os.stat(timers.FILE).st_mtime_ns)
            self.assertIsNone(self.armed[-1])
            self.assertEqual(len(claims), 2)
            for v in views:
                v.tick()
            self.assertEqual(len(claims), 2)
        self.assertEqual(hits, [[1], [1]])
        self.assertEqual(self.notified, [("tea", "timer · 00:16")])
        self.assertTrue(timers.load()[0]["fired"])

    def test_R_tv_07_nothing_pending(self):
        for records, changes in (((), 0), (({"id": "a", "due": 5, "fired": True},
                                            {"id": "b", "due": 6, "fired": True, "alarm": True}), 1)):
            with mock.patch.object(timers, "claim_due", side_effect=AssertionError("claimed")):
                t = self.make()
                hits = self.counter(t)
                self.store(*records)
                t.reload()
                t.tick()
            self.assertEqual(self.armed[-2:], [None, None])
            self.assertEqual(len(hits), changes)

    def test_R_tv_08_add(self):
        t = self.make()
        self.assertEqual(t.add("nonsense"), "try 25m, 1h30, 90s or 14:30")
        self.assertEqual(t.add("25m tea"), "")
        [stored] = timers.load()
        self.assertEqual({k: stored[k] for k in ("due", "label", "alarm", "fired")},
                         {"due": 2500.0, "label": "tea", "alarm": False, "fired": False})
        int(stored["id"], 16)
        self.assertEqual(t._seen, os.stat(timers.FILE).st_mtime_ns)
        self.assertEqual(t.items[0]["when"], "25m")
        err = OSError(28, "No space left on device", "/x/timers.json")
        with mock.patch.object(timers, "atomic_write", side_effect=err):
            # G-core-8: the whole exception, not just its strerror
            self.assertEqual(t.add("5m"), "can't save timers: [Errno 28] No space left on device: '/x/timers.json'")

    def test_R_tv_09_remove(self):
        self.store({"id": "a", "due": 2000}, {"id": "b", "due": 3000})
        t = self.make()
        t.remove("a")
        self.assertEqual([r["id"] for r in t.items], ["b"])
        self.assertEqual([x["id"] for x in timers.load()], ["b"])
        with mock.patch.object(timers, "atomic_write", side_effect=OSError("disk full")):
            t.remove("b")
        self.assertEqual([r["id"] for r in t.items], ["b"])


# ---------------------------------------------------------------- calendars list / cache

class CalendarFiles(HomeCase):
    def test_R_cal_01_R_cal_02_list(self):
        good = {"id": "good", "url": SECRET, "name": "Work", "color": "#4aa3ff"}
        write(calendars.LIST, json.dumps([None, {}, {"url": SECRET}, {"id": [], "url": SECRET},
                                          {"id": "", "url": SECRET}, {"id": "a", "url": []}, {"id": "b", "url": " "},
                                          {"id": "c", "url": SECRET, "name": {}},
                                          {"id": "d", "url": SECRET, "color": []}, good]))
        self.assertEqual(calendars.load_list(), [good])
        for bad in ("{", "{}", "null"):
            write(calendars.LIST, bad)
            self.assertEqual(calendars.load_list(), [])
        calendars.save_list([good])
        self.assertEqual(stat.S_IMODE(os.stat(calendars.LIST).st_mode), 0o600)
        self.assertTrue(read(calendars.LIST).startswith(b'[\n {\n  "id"'))

    def test_R_cal_03_palette(self):
        self.assertEqual(calendars.pick_color([]), "#4aa3ff")
        self.assertEqual(calendars.pick_color([{"color": "#4aa3ff"}, {"color": "#3ddc84"}]), "#f2b84b")
        full = [{"color": c} for c in calendars.PALETTE] + [{"color": "x"}]
        self.assertEqual(calendars.pick_color(full), calendars.PALETTE[7 % 6])

    def test_R_cal_04_ids(self):
        before = time.time_ns()
        self.assertGreaterEqual(int(timers.new_id(), 16), before)
        new = timers.new_id()
        self.assertEqual(new, new.lower())

    def test_R_cache_01_R_cache_02(self):
        ok_timed = {"uid": "m", "title": "Meeting", "start": 100.0, "end": 200.0, "allday": False, "x": 1}
        ok_day = {"uid": "h", "title": "Holiday", "start": "2026-11-01", "end": "2026-11-02", "allday": True}
        bad_events = [None, {"uid": 1, "title": "t", "start": 1, "end": 2, "allday": False},
                      {"uid": "u", "title": "t", "start": 1, "end": 2, "allday": 0},
                      {"uid": "u", "title": "t", "start": "2026-02-30", "end": "2026-03-01", "allday": True},
                      {"uid": "u", "title": "t", "start": "not-a-date", "end": "2026-03-01", "allday": True},
                      {"uid": "u", "title": "t", "start": None, "end": "2026-03-01", "allday": True},
                      {"uid": "u", "title": "t", "start": 1, "end": "2026-03-01", "allday": True},
                      {"uid": "u", "title": "t", "start": "x", "end": 2, "allday": False}]
        write(calendars.CACHE, json.dumps({"good": {"at": 5, "events": bad_events + [ok_timed, ok_day]},
                                           "legacy": {}, "null": None, "nullev": {"events": None},
                                           "badat": {"at": "x", "events": []}}))
        self.assertEqual(calendars.load_cache(), {"good": {"at": 5, "events": [ok_timed, ok_day]},
                                                  "legacy": {"events": []}})
        for bad in ("[]", "{"):
            write(calendars.CACHE, bad)
            self.assertEqual(calendars.load_cache(), {})
        calendars.save_cache({"a": {"at": 1, "events": []}})
        self.assertEqual(read(calendars.CACHE), b'{"a":{"at":1,"events":[]}}')
        self.assertEqual(stat.S_IMODE(os.stat(calendars.CACHE).st_mode), 0o600)


# ---------------------------------------------------------------- fetch

class FakeResponse:
    def __init__(self, body):
        self.body, self.limit = body, None

    def read(self, n):
        self.limit = n
        return self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class Fetch(unittest.TestCase):
    def fetch_with(self, effect, url=SECRET):
        requests = []

        def urlopen(req, timeout):
            requests.append((req, timeout))
            if isinstance(effect, BaseException):
                raise effect
            return effect
        with mock.patch.object(calendars.urllib.request, "urlopen", urlopen):
            try:
                return calendars.fetch(url), requests
            except FetchError as e:
                text = "".join(traceback.format_exception(e))
                self.assertNotIn("calendar.example", text)
                self.assertIsNone(e.__context__)
                self.assertIsNone(e.__cause__)
                return e, requests

    def test_R_fetch_01_schemes(self):
        body = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
        text, reqs = self.fetch_with(FakeResponse(body), "  WebCal://calendar.example/x.ics ")
        self.assertEqual(reqs[0][0].full_url, "https://calendar.example/x.ics")
        for url in ("ftp://calendar.example/x", "calendar.example", "file:///etc/passwd"):
            err, reqs = self.fetch_with(AssertionError("fetched"), url)
            self.assertEqual(str(err), "not an http(s) or webcal address")

    def test_R_fetch_02_R_fetch_03_request_and_content(self):
        resp = FakeResponse(b"\xffBEGIN:vcalendar\nEND:VCALENDAR")
        text, reqs = self.fetch_with(resp)
        req, timeout = reqs[0]
        self.assertEqual((req.get_header("User-agent"), timeout, resp.limit), ("atree", 20, 20_000_000))
        self.assertTrue(text.startswith("�BEGIN"))
        err, _ = self.fetch_with(FakeResponse(b" " * 2000 + b"BEGIN:VCALENDAR"))
        self.assertEqual(str(err), "that address doesn't serve an iCal calendar")

    def test_R_fetch_04_error_mapping(self):
        fp = mock.Mock()
        cases = [
            (urllib.error.HTTPError(SECRET, 403, "Forbidden " + SECRET, {}, fp), "the server answered HTTP 403"),
            (urllib.error.URLError(OSError(110, "Connection timed out")), "can't reach the server: Connection timed out"),
            (urllib.error.URLError(socket.gaierror(-2, "Name or service not known")),
             "can't reach the server: Name or service not known"),
            (urllib.error.URLError(TimeoutError()), "can't reach the server: timed out"),
            (urllib.error.URLError(socket.timeout(SECRET)), "can't reach the server: timed out"),
            (urllib.error.URLError("refused " + SECRET), "can't reach the server"),
            (urllib.error.URLError(ValueError(SECRET)), "can't reach the server"),
            (urllib.error.URLError(OSError(1, "see " + SECRET)), "can't reach the server"),
            (ValueError(SECRET), "download failed (ValueError)"),
            (OSError(SECRET), "download failed (OSError)"),
            (http.client.HTTPException(SECRET), "download failed (HTTPException)"),
        ]
        for effect, message in cases:
            err, _ = self.fetch_with(effect)
            self.assertIsInstance(err, FetchError)
            self.assertEqual(str(err), message)
        fp.close.assert_called()

    def test_R_fetch_04_real_http_errors(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", "file:///etc/passwd")
                else:
                    self.send_response(404, "Not Found " + SECRET)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):
                pass
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = "http://127.0.0.1:%d" % server.server_address[1]
        for path, message in (("/redirect", "the server answered HTTP 302"), ("/x", "the server answered HTTP 404")):
            with self.assertRaises(FetchError) as cm:
                calendars.fetch(base + path)
            self.assertEqual(str(cm.exception), message)
            self.assertIsNone(cm.exception.__context__)


# ---------------------------------------------------------------- agenda

def ts(h, m=0, day=2):
    return datetime(2026, 11, day, h, m, tzinfo=timezone.utc).timestamp()


class AgendaCase(ViewCase):
    def setUp(self):
        super().setUp()
        self.fetches = []
        self.fetch_result = FetchError("the server answered HTTP 500")
        for target, name, value in ((calendars, "fetch", self.fake_fetch),
                                    (clock.Agenda, "_spawn", lambda s, job: job())):
            p = mock.patch.object(target, name, value)
            p.start()
            self.addCleanup(p.stop)

    def fake_fetch(self, url):
        self.fetches.append(url)
        if isinstance(self.fetch_result, Exception):
            raise self.fetch_result
        return self.fetch_result

    def make(self):
        a = clock.Agenda()
        self.addCleanup(a.shutdown)
        return a

    def calendars_file(self, *ids, **extra):
        write(calendars.LIST, json.dumps([dict({"id": i, "name": i.title(), "url": SECRET + i}, **extra)
                                          for i in ids]))

    def cache_file(self, entries):
        write(calendars.CACHE, json.dumps(entries))

    def timed(self, uid, title, start, end):
        return {"uid": uid, "title": title, "start": start, "end": end, "allday": False}

    def assert_no_secret(self, agenda):
        blob = json.dumps([agenda.events, agenda.calendars, agenda.error]) + read(calendars.CACHE, "r")
        self.assertNotIn("private-abc123", blob)


class AgendaView(AgendaCase):
    def test_R_ag_01_R_ag_02_R_ag_08_startup_uses_fresh_cache(self):
        self.clock[0] = ts(9)
        self.calendars_file("c1", color="#ff7ab6")
        self.cache_file({"c1": {"at": ts(9) - 100, "events": [self.timed("m", "Meeting", ts(10), ts(11))]}})
        a = self.make()
        self.assertEqual(self.fetches, [])
        self.assertEqual(a._deadline, {"c1": ts(9) + 500})
        self.assertEqual(a.calendars, [{"id": "c1", "name": "C1", "color": "#ff7ab6", "failing": False}])
        self.assertEqual([r["title"] for r in a.events], ["Today", "Meeting"])
        with mock.patch.object(calendars, "load_list", side_effect=AssertionError("reloaded")):
            a.reload()
        self.assertAlmostEqual(self.armed[-1], ts(9) + 500)

    def test_R_ag_03_R_ag_05_R_ag_06_R_ag_07_rows(self):
        self.clock[0] = ts(10, 30)
        self.calendars_file("c1")
        self.cache_file({"c1": {"at": self.clock[0], "events": [
            self.timed("live", "Live", ts(10), ts(11)),
            self.timed("tom", "Tomorrow thing", ts(9, day=3), ts(10, day=3)),
            self.timed("far", "Far", ts(9, day=4), ts(10, day=4)),
            self.timed("past", "Past", ts(8), ts(9)),
            {"uid": "trip", "title": "Trip", "start": "2026-11-01", "end": "2026-11-04", "allday": True},
            {"uid": "b", "title": "B later", "start": ts(12), "end": ts(13), "allday": False},
            {"uid": "a", "title": "A later", "start": ts(12), "end": ts(13), "allday": False}]}})
        a = self.make()
        self.assertEqual(a.events, [
            {"uid": "Today", "head": True, "title": "Today", "now": False},
            {"head": False, "title": "Trip", "color": "#4aa3ff", "uid": "c1/trip/2026-11-01/0", "time": "all day",
             "now": False, "sort": 0},
            {"head": False, "title": "Live", "color": "#4aa3ff", "uid": "c1/live/%s" % ts(10), "now": True,
             "time": "10:00", "until": "until 11:00", "sort": ts(10)},
            {"head": False, "title": "A later", "color": "#4aa3ff", "uid": "c1/a/%s" % ts(12), "now": False,
             "time": "12:00", "until": "", "sort": ts(12)},
            {"head": False, "title": "B later", "color": "#4aa3ff", "uid": "c1/b/%s" % ts(12), "now": False,
             "time": "12:00", "until": "", "sort": ts(12)},
            {"uid": "Tomorrow", "head": True, "title": "Tomorrow", "now": False},
            {"head": False, "title": "Trip", "color": "#4aa3ff", "uid": "c1/trip/2026-11-01/1", "time": "all day",
             "now": False, "sort": 0},
            {"head": False, "title": "Tomorrow thing", "color": "#4aa3ff", "uid": "c1/tom/%s" % ts(9, day=3),
             "now": False, "time": "09:00", "until": "", "sort": ts(9, day=3)}])
        hits = self.counter(a)
        a.tick()
        self.assertEqual(hits, [])
        self.assertAlmostEqual(self.armed[-1], self.clock[0] + 600)

    def test_R_ag_09_no_calendars_disarms(self):
        a = self.make()
        self.assertIsNone(self.armed[-1])
        self.assertEqual((a.events, a.calendars), ([], []))

    def test_R_ag_10_zero_length_event(self):
        start = ts(12)
        self.clock[0] = start
        self.calendars_file("c1")
        self.cache_file({"c1": {"at": start, "events": [self.timed("z", "Zero", start, start)]}})
        a = self.make()
        hits = self.counter(a)
        self.assertEqual([r["title"] for r in a.events], ["Today", "Zero"])
        self.assertEqual(self.armed[-1], start + 60)
        for now, armed, n_changes, rows in ((start + 59, start + 60, 0, 2), (start + 60, start + 600, 1, 0),
                                            (start + 60.05, start + 600, 1, 0)):
            self.clock[0] = now
            a.tick()
            self.assertEqual(self.armed[-1], armed, now)
            self.assertEqual(len(hits), n_changes, now)
            self.assertEqual(len(a.events), rows)
        self.assertEqual(self.fetches, [])

    def test_R_ag_04_R_ag_11_R_ag_12_fetch_round(self):
        self.clock[0] = 1000
        self.calendars_file("c1")
        self.fetch_result = "BEGIN:VCALENDAR\nEND:VCALENDAR"
        events = [self.timed("e", "Fetched", 1100, 1200)]
        with mock.patch.object(ical, "instances", lambda text: events):
            a = self.make()
        self.assertEqual(self.fetches, [SECRET + "c1"])
        self.assertEqual(a._cache["c1"], {"at": 1000, "events": events})
        self.assertEqual(a._deadline, {"c1": 1600})
        self.assertEqual(json.loads(read(calendars.CACHE))["c1"]["events"], events)
        self.assertIn("Fetched", [r["title"] for r in a.events])
        self.assertFalse(a._running)
        self.assert_no_secret(a)

    def test_R_ag_11_round_results(self):
        self.cache_file({"fresh": {"at": 1000 - 100, "events": []}, "stale": {"at": 1000 - 580, "events": []}})
        self.fetch_result = ValueError("bad " + SECRET)
        with mock.patch.object(clock, "_now", lambda: 1000):
            results = clock.Agenda._fetch_round([("fresh", SECRET), ("stale", SECRET), ("err", SECRET)])
        self.assertEqual(results["fresh"], {"at": 900, "events": []})
        for cid in ("stale", "err"):
            self.assertIsInstance(results[cid], FetchError)
            self.assertEqual(str(results[cid]), "can't read that calendar (ValueError)")
        self.fetch_result = FetchError("the server answered HTTP 401")
        self.assertEqual(str(clock.Agenda._fetch_round([("x", SECRET)])["x"]), "the server answered HTTP 401")

    def test_R_ag_12_failures_back_off(self):
        self.clock[0] = 1000
        self.calendars_file("c1")
        self.cache_file({"c1": {"at": 1000, "events": [self.timed("k", "Kept", 1100, 1200)]}})
        a = self.make()
        a._fails["c1"] = 2
        a._running = True
        a._on_round({"c1": FetchError("x")}, now=1000)
        self.assertEqual((a._fails, a._deadline, a._running), ({"c1": 3}, {"c1": 1240}, False))
        self.assertEqual(a.calendars[0]["failing"], True)
        self.assertEqual(a._cache["c1"]["events"][0]["title"], "Kept")
        a._fails.clear()
        a._on_round({"c1": FetchError("x")}, now=1000)
        self.assertEqual(a._deadline, {"c1": 1060})
        a._on_round({"c1": {"at": 1000, "events": [self.timed("n", "New", 1100, 1200)]}}, now=1000)
        self.assertEqual((a._fails, a._deadline), ({}, {"c1": 1600}))
        self.assertIn("New", [r["title"] for r in a.events])
        a._fails["c1"] = 40
        a._on_round({"c1": FetchError("x")}, now=1000)
        self.assertEqual(a._deadline, {"c1": 4600})

    def removal_setup(self):
        self.clock[0] = 1000
        self.calendars_file("removed", "kept")
        self.cache_file({"kept": {"at": 9400, "events": []}, "removed": {"at": 9400, "events": []}})
        a = self.make()
        a._fails.update(removed=2, kept=1)
        a._running = True
        a.removeCalendar("removed")
        return a

    def test_R_ag_19_remove(self):
        a = self.removal_setup()
        self.assertEqual((a._deadline, a._fails, a._running), ({"kept": 10000}, {"kept": 1}, True))
        self.assertNotIn("removed", a._cache)
        self.assertNotIn("removed", json.loads(read(calendars.CACHE)))
        self.assertEqual([c["id"] for c in calendars.load_list()], ["kept"])
        self.assertEqual(self.fetches, [])
        with mock.patch.object(calendars, "save_list", side_effect=OSError(28, "disk full")):
            a.removeCalendar("kept")
        self.assertEqual(a.error, "can't save calendars: disk full")

    def test_R_ag_13_late_result_for_removed(self):
        for result in ({"at": 1000, "events": []}, FetchError("x")):
            a = self.removal_setup()
            a._fails.clear()
            a._on_round({"removed": result}, now=1000)
            self.assertFalse(a._running)
            self.assertEqual((a._deadline, a._fails), ({"kept": 10000}, {}))
            self.assertNotIn("removed", a._cache)
            self.assertNotIn("removed", json.loads(read(calendars.CACHE)))
            self.assertEqual(self.armed[-1], 10000)
            for now in (1601, 1601.05):
                a.tick(now)
                self.assertEqual(self.armed[-1], 10000)
            self.assertEqual(self.fetches, [])

    def test_R_ag_14_cache_merge_and_save_failure(self):
        self.clock[0] = 1000
        self.calendars_file("kept")
        self.cache_file({"kept": {"at": 900, "events": []}, "gone": {"at": 950, "events": []}})
        a = self.make()
        hits = self.counter(a)
        with mock.patch.object(calendars, "save_cache", side_effect=OSError(28, "disk full")):
            for _ in range(2):
                a._on_round({"kept": {"at": 1000, "events": []}}, now=1000)
        self.assertEqual(a.error, "can't save calendar cache: disk full")
        self.assertEqual(a._deadline, {"kept": 1600})
        self.assertEqual(len(hits), 1)
        self.cache_file({"kept": {"at": 2000, "events": [self.timed("d", "Disk", 2100, 2200)]}})
        a._save_cache()
        self.assertEqual(a._cache, {"kept": {"at": 2000, "events": [self.timed("d", "Disk", 2100, 2200)]}})
        self.assertEqual(set(json.loads(read(calendars.CACHE))), {"kept"})

    def test_R_ag_21_R_cal_02_startup_with_invalid_cache(self):
        self.clock[0] = ts(9)
        write(calendars.LIST, json.dumps([None, {"url": SECRET}, {"id": "good", "url": SECRET, "name": "Good"}]))
        self.cache_file({"good": {"at": ts(9) - 60, "events": [
            None, {"uid": "x", "title": "Bad", "start": "2026-02-30", "end": "2026-03-01", "allday": True},
            self.timed("m", "Meeting", ts(10), ts(11)),
            {"uid": "h", "title": "Holiday", "start": "2026-11-02", "end": "2026-11-03", "allday": True}]},
            "null": None})
        a = self.make()
        self.assertEqual([c["id"] for c in a.calendars], ["good"])
        self.assertEqual([r["title"] for r in a.events], ["Today", "Holiday", "Meeting"])
        self.assertEqual(self.fetches, [])


class Reminders(AgendaCase):
    def setup_event(self, now_offset=-3600):
        self.start = ts(12)
        self.clock[0] = self.start + now_offset
        self.calendars_file("c")
        self.cache_file({"c": {"at": self.clock[0], "events": [self.timed("m", "Meeting", self.start, ts(13))]}})
        a = self.make()
        a._deadline["c"] = 1e12
        return a

    def claims(self, *outcomes):
        calls = []
        seq = list(outcomes)

        def claim(key):
            calls.append(key)
            r = seq.pop(0) if len(seq) > 1 else seq[0]
            if isinstance(r, Exception):
                raise r
            return r
        p = mock.patch.object(timers, "claim_once", claim)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def test_R_rem_01_R_rem_03_retry_then_notify(self):
        a = self.setup_event()
        calls = self.claims(OSError(28, "disk full"), True)
        now = self.start - 120
        for expect_err in (True, False, False):
            a.tick(now)
        self.assertEqual(calls, ["event:c:m:%r" % float(self.start)] * 2)
        self.assertEqual(a.error, "can't save reminders: disk full")
        self.assertEqual(self.notified, [("Meeting", "in 2 min · 12:00")])

    def test_R_rem_02_R_rem_04_lost_claim_or_failing_notification(self):
        for won in (False, True):
            a = self.setup_event()
            self.notified.clear()
            calls = self.claims(won)
            with mock.patch.object(timers, "notify", side_effect=RuntimeError("no dbus")) as notify:
                a.tick(self.start - 120)
                a.tick(self.start - 110)
            self.assertEqual(len(calls), 1)
            self.assertEqual(notify.call_count, 1 if won else 0)
            self.assertIn(calls[0], a._handled)
            self.assertEqual(a.error, "")

    def test_R_rem_02_rounding(self):
        a = self.setup_event()
        self.claims(True)
        a.tick(self.start - 150)
        self.assertEqual(self.notified, [("Meeting", "in 2 min · 12:00")])

    def test_R_rem_05_retry_timing(self):
        a = self.setup_event()
        hits = self.counter(a)
        calls = self.claims(OSError(28, "disk full"), OSError(28, "disk full"), True)
        a.tick(self.start - 120)
        self.assertEqual(self.armed[-1], self.start - 90)
        self.assertEqual(len(hits), 1)  # the error; the rows were already shown before this tick
        a.tick(self.start - 90)
        self.assertEqual(self.armed[-1], self.start - 60)
        a.tick(self.start - 60)
        self.assertEqual(self.armed[-1], self.start)
        a.tick(self.start - 59)
        self.assertEqual(self.armed[-1], self.start)
        self.assertEqual((len(calls), len(self.notified), len(hits)), (3, 1, 1))
        self.assertEqual(self.fetches, [])

    def test_R_rem_06_retry_never_delays(self):
        a = self.setup_event()
        self.claims(OSError(28, "disk full"))
        now = self.start - 120
        a._deadline["c"] = now + 10
        a.tick(now)
        self.assertEqual(self.armed[-1], now + 10)
        a._deadline["c"] = 1e12
        a._cache["c"]["events"].append(self.timed("l", "Live", now - 60, now + 5))
        a.tick(now)
        self.assertEqual(self.armed[-1], now + 5)
        self.assertEqual(self.notified, [])

    def test_R_rem_07_near_the_start(self):
        a = self.setup_event()
        hits = self.counter(a)
        calls = self.claims(OSError(28, "disk full"))
        a.tick(self.start - 15)
        self.assertEqual(self.armed[-1], self.start - 1)
        for now in (self.start - 1, self.start - 0.5):
            a.tick(now)
            self.assertEqual(self.armed[-1], self.start)
        self.assertEqual((len(calls), len(hits)), (3, 1))  # one error change; rows predate the ticks
        a.tick(self.start)
        self.assertEqual(self.armed[-1], ts(13))
        self.assertEqual((len(calls), self.notified), (3, []))


class Adding(AgendaCase):
    def setUp(self):
        super().setUp()
        self.clock[0] = ts(9)
        self.events = [self.timed("m", "Standup", ts(10), ts(11))]
        p = mock.patch.object(ical, "instances", lambda text: self.events)
        p.start()
        self.addCleanup(p.stop)
        self.fetch_result = "BEGIN:VCALENDAR\nX-WR-CALNAME:Work cal\nEND:VCALENDAR"

    def test_R_ag_15_ignored_and_duplicate(self):
        a = self.make()
        hits = self.counter(a)
        a.addCalendar("   ", "x")
        a._state["adding"] = True
        a.addCalendar(SECRET, "x")
        a._state["adding"] = False
        self.assertEqual((self.fetches, hits), ([], []))
        self.calendars_file("c1")
        self.cache_file({"c1": {"at": ts(9), "events": []}})
        a._cache = calendars.load_cache()
        a.reload()
        a.addCalendar(" %s " % (SECRET + "c1"), "dup")
        self.assertEqual(a.error, "that calendar is already added")
        self.assertEqual(self.fetches, [])

    def test_R_ag_15_R_ag_16_errors(self):
        a = self.make()
        states = []
        a.changed.connect(lambda: states.append((a.adding, a.error)))
        self.fetch_result = FetchError("the server answered HTTP 403")
        a.addCalendar(SECRET, "")
        self.assertEqual(states, [(True, ""), (False, "the server answered HTTP 403")])
        self.fetch_result = "BEGIN:VCALENDAR"
        with mock.patch.object(ical, "instances", side_effect=KeyError(SECRET)):
            a.addCalendar(SECRET, "")
        self.assertEqual(a.error, "can't read that calendar (KeyError)")
        self.assertFalse(os.path.exists(calendars.LIST))
        if os.path.exists(calendars.CACHE):
            self.assert_no_secret(a)

    def test_R_ag_17_R_ag_18_R_ag_20_success(self):
        write(calendars.LIST, json.dumps([{"id": "other", "url": "https://x/other", "color": "#4aa3ff"}]))
        self.cache_file({"other": {"at": ts(9), "events": []}})
        a = self.make()
        states = []
        a.changed.connect(lambda: states.append((a.adding, a.error)))
        a.addCalendar(SECRET, "  ")
        cals = calendars.load_list()
        self.assertEqual([c["url"] for c in cals], ["https://x/other", SECRET])
        new = cals[1]
        self.assertEqual((new["name"], new["color"]), ("Work cal", "#f2b84b"))
        self.assertEqual(stat.S_IMODE(os.stat(calendars.LIST).st_mode), 0o600)
        self.assertEqual(a._cache[new["id"]], {"at": ts(9), "events": self.events})
        self.assertEqual(a._deadline[new["id"]], ts(9) + 600)
        self.assertEqual(self.fetches, [SECRET])
        self.assertIn("Standup", [r["title"] for r in a.events])
        self.assertEqual([c["name"] for c in a.calendars], ["", "Work cal"])
        self.assertEqual((a.adding, a.error), (False, ""))
        self.assertTrue(all(s[0] for s in states[:-1]))
        self.assertEqual(states[-1], (False, ""))
        self.assert_no_secret(a)
        a.reload()
        b = self.make()
        self.assertEqual(b.events, a.events)
        self.assertEqual(self.fetches, [SECRET])

    def test_R_ag_17_names(self):
        a = self.make()
        a.addCalendar(SECRET + "1", " My   name ")
        self.fetch_result = "BEGIN:VCALENDAR\nEND:VCALENDAR"
        a.addCalendar(SECRET + "2", "")
        self.assertEqual([c["name"] for c in calendars.load_list()], ["My name", "Calendar"])

    def test_R_ag_17_list_save_failure(self):
        self.calendars_file("old")
        self.cache_file({"old": {"at": ts(9), "events": []}})
        a = self.make()
        rows, cache_before = a.events, dict(a._cache)
        with mock.patch.object(calendars, "save_list", side_effect=OSError(28, "disk full")):
            a.addCalendar(SECRET, "")
        self.assertEqual((a.adding, a.error), (False, "can't save calendars: disk full"))
        self.assertEqual(a._cache, cache_before)
        self.assertEqual(a.events, rows)
        self.assertEqual([c["id"] for c in calendars.load_list()], ["old"])

    def test_R_ag_17_cache_save_failure_is_silent(self):
        a = self.make()
        with mock.patch.object(calendars, "save_cache", side_effect=OSError(28, "disk full")):
            a.addCalendar(SECRET, "")
        self.assertEqual((a.adding, a.error), (False, ""))
        self.assertIn("Standup", [r["title"] for r in a.events])
        self.assertFalse(os.path.exists(calendars.CACHE))


class FolderWatcher(HomeCase):
    def test_R_sleep_10_shared_watcher_triggers_reloads(self):
        set_tz(self, "UTC")
        t, a = clock.Timers(), clock.Agenda()
        self.addCleanup(t.shutdown)
        self.addCleanup(a.shutdown)
        self.assertIs(clock.folder_watcher(), clock.folder_watcher())
        self.assertIs(clock.folder_watcher().parent(), app)
        with mock.patch.object(calendars, "fetch", side_effect=FetchError("offline")):
            write(timers.FILE, json.dumps({"timers": [{"id": "w", "due": time.time() + 600}]}))
            write(calendars.LIST, json.dumps([{"id": "cal", "url": SECRET}]))
            ok = spin(1.0, lambda: t.items and a.calendars)
        self.assertTrue(ok)
        self.assertEqual(t.items[0]["id"], "w")
        self.assertEqual(a.calendars[0]["id"], "cal")


if __name__ == "__main__":
    unittest.main()
