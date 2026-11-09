"""Timer storage, cross-process claiming, notification and text parsing (spec clock.md §3, §4)."""
import json
import os
import signal
import subprocess
import sys
import time
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from clocksupport import ROOT, HomeCase, read, set_tz, write

from atree import timers

INVALID_TIMERS = [None, {}, {"id": [], "due": 1}, {"id": "", "due": 1}, {"id": "n", "due": None},
                  {"id": "t", "due": True}, {"id": "s", "due": "soon"}, {"id": "l", "due": []},
                  {"id": "o", "due": {}}, {"id": "big", "due": 1e300}, {"id": "lab", "due": 5, "label": []},
                  {"id": "al", "due": 5, "alarm": "false"}, {"id": "fi", "due": 5, "fired": 1}]
INVALID_JSON_VALUES = ["NaN", "Infinity", "-Infinity", "1" + "0" * 400]


def raw_timers(records, claimed=None, extra_due=()):
    """JSON text, with literal invalid numbers spliced in as extra timers."""
    body = json.dumps({"timers": records, "claimed": claimed or {}}, indent=1)
    for i, v in enumerate(extra_due):
        body = body.replace('"timers": [', '"timers": [{"id": "x%d", "due": %s}, ' % (i, v), 1)
    return body


class Storage(HomeCase):
    def test_R_tstore_01_unusable_files_load_empty(self):
        self.assertEqual(timers.load(), [])
        for content in (b"\xff", b"{", b"null", b"false", b"1", b'"text"', b"[]", b"{}"):
            write(timers.FILE, content)
            self.assertEqual(timers._read(), ([], {}), content)

    def test_R_tstore_02_wrong_member_types(self):
        for bad in ("null", "false", "1", '"text"', "{}"):
            write(timers.FILE, '{"timers": %s, "claimed": %s}' % (bad, "[]" if bad == "{}" else bad))
            self.assertEqual(timers.claim_due(100), ([], []))
        write(timers.FILE, '{"timers": 1, "claimed": "x"}')
        self.assertTrue(timers.claim_once("event"))
        self.assertFalse(timers.claim_once("event"))

    def test_R_tstore_03_R_tstore_05_validation_keeps_valid_records_verbatim(self):
        good = {"id": "g", "due": 5, "unknown": [1], "fired": False}
        write(timers.FILE, raw_timers(INVALID_TIMERS + [good], {"ok": 3, "bad": None},
                                      extra_due=INVALID_JSON_VALUES))
        self.assertEqual(timers.load(), [good])
        timers.edit(lambda ts: None)
        saved = json.loads(read(timers.FILE))
        self.assertEqual(saved, {"timers": [good], "claimed": {"ok": 3}})

    def test_R_tstore_04_claims_need_valid_timestamps(self):
        write(timers.FILE, '{"claimed": {"a": 1.5, "b": true, "c": "x", "d": NaN, "e": 1e300, "f": null}}')
        self.assertEqual(timers._read()[1], {"a": 1.5})

    def test_R_tstore_06_lock_creates_dir_and_is_exclusive(self):
        with timers.lock():
            self.assertTrue(os.path.exists(timers.LOCK))
            code = ("import fcntl,os,sys; fd=os.open(sys.argv[1], os.O_RDWR)\n"
                    "try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)\n"
                    "except BlockingIOError: sys.exit(3)\n")
            self.assertEqual(subprocess.run([sys.executable, "-c", code, timers.LOCK]).returncode, 3)

    def test_R_tstore_07_edit(self):
        self.assertEqual(timers.edit(lambda ts: ts.append({"id": "a", "due": 1})), [{"id": "a", "due": 1}])
        self.assertEqual(timers.edit(lambda ts: [{"id": "b", "due": 2}]), [{"id": "b", "due": 2}])
        self.assertEqual(timers.load(), [{"id": "b", "due": 2}])
        self.assertEqual(json.loads(read(timers.FILE))["claimed"], {})
        self.assertTrue(read(timers.FILE).startswith(b'{\n "timers"'))

    def test_R_tstore_08_claim_due(self):
        timers.edit(lambda ts: [{"id": "due", "due": 90}, {"id": "future", "due": 110},
                                {"id": "old", "due": 50, "fired": True}])
        before = os.stat(timers.FILE).st_mtime_ns
        mine, all_ = timers.claim_due(100)
        self.assertEqual(mine, [{"id": "due", "due": 90, "fired": True}])
        self.assertEqual(len(all_), 3)
        os.utime(timers.FILE, ns=(before, before))
        self.assertEqual(timers.claim_due(100)[0], [])
        self.assertEqual(os.stat(timers.FILE).st_mtime_ns, before)

    def test_R_tstore_09_claim_once_prunes(self):
        now = time.time()
        write(timers.FILE, json.dumps({"timers": [], "claimed": {"old": now - 172900, "recent": now - 1000}}))
        self.assertTrue(timers.claim_once("k"))
        self.assertEqual(set(json.loads(read(timers.FILE))["claimed"]), {"recent", "k"})
        mtime = os.stat(timers.FILE).st_mtime_ns
        self.assertFalse(timers.claim_once("k"))
        self.assertEqual(os.stat(timers.FILE).st_mtime_ns, mtime)

    def test_R_tstore_10_two_processes(self):
        write(timers.FILE, json.dumps({"timers": [{"id": "due", "due": 90}, {"id": "future", "due": 110},
                                                  {"id": "bad", "due": None}], "claimed": {"bad": None}}))
        start = time.time() + 0.5
        code = ("import json,sys,time; sys.path.insert(0, %r)\n"
                "from atree import timers\n"
                "timers.DATA, timers.FILE, timers.LOCK = sys.argv[1:4]\n"
                "time.sleep(max(0, %r - time.time()))\n"
                "mine, _ = timers.claim_due(100)\n"
                "print(json.dumps([[t['id'] for t in mine], timers.claim_once('same-event')]))\n") % (ROOT, start)
        args = [sys.executable, "-c", code, timers.DATA, timers.FILE, timers.LOCK]
        procs = [subprocess.Popen(args, stdout=subprocess.PIPE) for _ in range(2)]
        results = [json.loads(p.communicate(timeout=30)[0]) for p in procs]
        self.assertEqual(sorted(r[0] for r in results), [[], ["due"]])
        self.assertEqual(sorted(r[1] for r in results), [False, True])
        self.assertEqual(timers.load(), [{"id": "due", "due": 90, "fired": True}, {"id": "future", "due": 110}])
        self.assertFalse(timers.claim_once("same-event"))

    def test_R_tstore_11_mixed_file(self):
        now = time.time()
        future = {"id": "future", "due": now + 3600}
        done = {"id": "done", "due": now - 10, "fired": True, "label": "tea"}
        write(timers.FILE, raw_timers(INVALID_TIMERS + [future, done], {"existing": now, "b": True, "c": None},
                                      extra_due=INVALID_JSON_VALUES))
        self.assertEqual(timers.load(), [future, done])
        self.assertEqual(timers.claim_due(now), ([], [future, done]))
        self.assertFalse(timers.claim_once("existing"))
        self.assertTrue(timers.claim_once("new"))
        self.assertEqual(set(json.loads(read(timers.FILE))["claimed"]), {"existing", "new"})


class Notify(unittest.TestCase):
    def run_notify(self, available, sound_file=True, **kw):
        started = []
        with mock.patch.object(timers.shutil, "which", lambda n: "/bin/" + n if n in available else None), \
                mock.patch.object(timers.os.path, "exists", lambda p: sound_file), \
                mock.patch.object(timers.subprocess, "Popen", lambda argv, **k: started.append((argv, k)) or
                                  mock.Mock(wait=mock.Mock(return_value=0))):
            timers.notify("Title", "Body", **kw)
        return started

    def test_R_notify_01_commands(self):
        started = self.run_notify({"notify-send", "canberra-gtk-play", "paplay"})
        self.assertEqual([a for a, _ in started], [
            ["notify-send", "-a", "atree", "-i", "alarm-symbolic", "Title", "Body"],
            ["canberra-gtk-play", "-i", "alarm-clock-elapsed", "-d", "atree"]])
        for _, k in started:
            self.assertEqual((k["stdin"], k["stdout"], k["stderr"]), (subprocess.DEVNULL,) * 3)
        self.assertEqual([a for a, _ in self.run_notify({"paplay"})], [["paplay", timers.SOUND]])
        self.assertEqual(self.run_notify({"paplay"}, sound_file=False), [])
        self.assertEqual(len(self.run_notify({"notify-send", "paplay"}, sound=False)), 1)

    def test_R_notify_01_never_raises(self):
        with mock.patch.object(timers.shutil, "which", side_effect=RuntimeError):
            timers.notify("a", "b")
        with mock.patch.object(timers.shutil, "which", lambda n: "/x"), \
                mock.patch.object(timers.subprocess, "Popen", side_effect=OSError):
            timers.notify("a", "b")

    def test_spawn_timeout_kills_group_and_reaps(self):
        for gone in (False, True):
            with self.subTest(gone=gone):
                proc = mock.Mock(pid=12345)
                proc.wait.side_effect = [subprocess.TimeoutExpired("player", 30), -signal.SIGKILL]
                calls = mock.Mock()
                calls.attach_mock(proc.wait, "wait")
                with mock.patch.object(timers.subprocess, "Popen", return_value=proc) as popen, \
                        mock.patch.object(timers.threading, "Thread") as thread, \
                        mock.patch.object(timers.os, "killpg") as kill:
                    calls.attach_mock(kill, "kill")
                    if gone:
                        kill.side_effect = ProcessLookupError
                    timers._spawn(["player"])
                    self.assertTrue(popen.call_args.kwargs["start_new_session"])
                    thread.return_value.start.assert_called_once_with()
                    thread.call_args.kwargs["target"]()
                self.assertEqual(calls.mock_calls, [mock.call.wait(30),
                                 mock.call.kill(proc.pid, signal.SIGKILL), mock.call.wait()])


class Parse(unittest.TestCase):
    def setUp(self):
        set_tz(self, "America/New_York")

    def countdown(self, text, now=1000.0):
        return timers.parse(text, now)

    def test_R_tparse_01_R_tparse_06_countdowns(self):
        for text, secs, label in (("25m tea", 1500, "tea"), ("1h30", 5400, ""), ("90s", 90, ""),
                                  ("1h 5m lunch break", 3900, "lunch break"), ("5", 300, ""),
                                  ("1min 5sec", 65, ""), ("10000000000s", 10_000_000_000, ""),
                                  ("1H5M  Tea   time", 3900, "Tea time")):
            self.assertEqual(self.countdown(text), {"due": 1000.0 + secs, "alarm": False, "label": label}, text)

    def test_R_tparse_02_number_pair_guard(self):
        self.assertEqual(self.countdown("5 10"), {"due": 1300.0, "alarm": False, "label": "10"})
        self.assertEqual(self.countdown("5 10 15"), {"due": 1300.0, "alarm": False, "label": "10 15"})

    def test_R_tparse_unicode_digits(self):
        # G-core-11: any Unicode decimal digit counts, in the patterns and the number-pair guard
        self.assertEqual(self.countdown("\u0663"), {"due": 1180.0, "alarm": False, "label": ""})
        self.assertEqual(self.countdown("\u0661h\u0663\u0660m tea"), {"due": 6400.0, "alarm": False, "label": "tea"})
        self.assertEqual(self.countdown("\uff15 \uff11\uff10"), {"due": 1300.0, "alarm": False, "label": "\uff11\uff10"})
        self.assertEqual(self.countdown("\u0967 \u0968m"), {"due": 1060.0, "alarm": False, "label": "\u0968m"})
        got = timers.parse("\u0661\u0664:\u0663\u0660", self.at(6, 1, 12))
        self.assertEqual((got["due"], got["alarm"]), (self.at(6, 1, 14, 30), True))
        self.assertIsNone(self.countdown("\u00b2"))  # a digit, but not a decimal one

    def test_R_tparse_04_zero_tries_shorter(self):
        self.assertEqual(self.countdown("1h 0m"), {"due": 4600.0, "alarm": False, "label": ""})
        self.assertIsNone(self.countdown("0"))
        self.assertIsNone(self.countdown("0m tea"))

    def test_R_tparse_05_nothing(self):
        for text in ("", "   ", "tea", "m", "1x", "1h30m5"):
            self.assertIsNone(self.countdown(text), text)

    def test_R_tparse_07_huge_numbers(self):
        for zeros in (100, 400, 5000):
            for suffix in ("s", "h", ""):
                self.assertIsNone(self.countdown("1" + "0" * zeros + suffix), (zeros, suffix))

    def at(self, *args, fold=0):
        return datetime(2026, *args, tzinfo=ZoneInfo("America/New_York"), fold=fold).timestamp()

    def test_R_tparse_03_R_tparse_08_alarms(self):
        rows = [((6, 1, 12), "14:30", (6, 1, 14, 30), 0), ((6, 1, 14, 30), "14.30", (6, 2, 14, 30), 0),
                ((6, 1, 15), "14:30", (6, 2, 14, 30), 0),
                ((11, 1, 0, 45), "1:30 call", (11, 1, 1, 30), 0),
                ((11, 1, 1, 30), "1:30 call", (11, 1, 1, 30), 1),
                ((11, 1, 1, 45), "1:30 call", (11, 1, 1, 30), 1),
                ((11, 1, 1, 15, 0, 0, 1), "1:30 call", (11, 1, 1, 30), 1),
                ((11, 1, 1, 30, 0, 0, 1), "1:30", (11, 2, 1, 30), 0),
                ((11, 1, 1, 45, 0, 0, 1), "1:30", (11, 2, 1, 30), 0),
                ((3, 8, 1), "2:30", (3, 8, 3, 30), 0), ((3, 8, 3), "2:30", (3, 8, 3, 30), 0)]
        for now, text, due, fold in rows:
            now_fold = now[6] if len(now) > 6 else 0
            now_t = self.at(*now[:6], fold=now_fold)
            got = timers.parse(text, now_t)
            self.assertEqual(got["due"], self.at(*due, fold=fold), (now, text))
            self.assertTrue(got["alarm"])
            self.assertEqual(got["label"], text.partition(" ")[2])

    def test_R_tparse_03_out_of_range_stops(self):
        self.assertIsNone(timers.parse("24:00 5m", 1000.0))
        self.assertIsNone(timers.parse("12:60", 1000.0))

    def test_R_tparse_09_countdown_is_elapsed(self):
        now = self.at(11, 1, 1, 45)
        self.assertEqual(timers.parse("1h30 tea", now), {"due": now + 5400, "alarm": False, "label": "tea"})

    def test_R_tz_02_follows_tz(self):
        now = self.at(6, 1, 12)
        ny = timers.parse("14:30", now)["due"]
        set_tz(self, "Europe/Berlin")
        self.assertNotEqual(timers.parse("14:30", now)["due"], ny)
        self.assertEqual(timers.hhmm(now), "18:00")


if __name__ == "__main__":
    unittest.main()
