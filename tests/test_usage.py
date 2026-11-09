"""Plan usage: the aggregator and the Claude/Codex sources it relies on (spec clock.md §12)."""
import json
import os
import threading
import time
import unittest
from unittest import mock

from clocksupport import HomeCase, write

from atree import statusline, usage
from atree.agents import claude, codex


class Fake:
    def __init__(self, name, result, delay=0):
        self.name, self.result, self.delay, self.calls = name, result, delay, 0

    def usage(self):
        self.calls += 1
        time.sleep(self.delay)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class Aggregator(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(usage, "_cached", None)
        p.start()
        self.addCleanup(p.stop)

    def agents(self, *agents):
        p = mock.patch.object(usage, "_agents", lambda: list(agents))
        p.start()
        self.addCleanup(p.stop)

    def test_R_use_01_shape_errors_and_omission(self):
        bars = {"bars": [], "observed_at": 1}
        self.agents(Fake("claude", bars), Fake("codex", ValueError("broken log")), Fake("gemini", None))
        got = usage.get()
        self.assertEqual(set(got), {"claude", "codex", "at"})
        self.assertEqual(got["claude"], bars)
        self.assertEqual(got["codex"], {"error": "broken log"})
        self.assertAlmostEqual(got["at"], time.time(), delta=5)

    def test_R_use_01_cache_and_force(self):
        a = Fake("claude", {"bars": []})
        self.agents(a)
        first = usage.get()
        self.assertEqual(usage.get(), first)
        self.assertEqual(a.calls, 1)
        usage.get(force=True)
        self.assertEqual(a.calls, 2)
        with mock.patch.object(usage.time, "time", return_value=first["at"] + 51):
            usage.get()
        self.assertEqual(a.calls, 3)

    def test_R_use_01_one_computation_at_a_time(self):
        a = Fake("claude", {"bars": []}, delay=0.1)
        self.agents(a)
        threads = [threading.Thread(target=usage.get) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(a.calls, 1)


class ClaudeUsage(HomeCase):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(claude, "CLAUDE_DIR", self.claude)
        p.start()
        self.addCleanup(p.stop)

    def test_R_use_02_hints(self):
        self.assertEqual(claude.Claude().usage(), {"error": "run atree.py --install-statusline"})
        write(statusline.SETTINGS, json.dumps({"statusLine": {"type": "command", "command": "/opt/atree.py --statusline"}}))
        self.assertEqual(claude.Claude().usage(), {"error": "waiting for Claude Code's status line"})

    def test_R_use_03_bars(self):
        now = time.time()
        write(statusline.LIMITS, json.dumps({"at": 123.5, "rate_limits": {
            "five_hour": {"used_percentage": 12, "resets_at": now + 600},
            "seven_day": {"used_percentage": 40.5, "resets_at": "2000-01-01T00:00:00Z"},
            "other": {"used_percentage": 1}}}))
        got = claude.Claude().usage()
        self.assertEqual(got["observed_at"], 123.5)
        self.assertEqual(got["bars"], [
            {"label": "session (5h)", "used": 12.0, "window": 18000, "resets_at": now + 600, "stale": False},
            {"label": "week", "used": 0.0, "window": 604800, "resets_at": 946684800.0, "stale": True}])
        write(statusline.LIMITS, json.dumps({"at": 1, "rate_limits": {
            "five_hour": {"used_percentage": "x"}, "seven_day": {"used_percentage": 5, "resets_at": "junk"}}}))
        self.assertEqual(claude.Claude().usage()["bars"],
                         [{"label": "week", "used": 5.0, "window": 604800, "resets_at": None, "stale": False}])


def token_line(ts, limits):
    return json.dumps({"timestamp": ts, "type": "event_msg",
                       "payload": {"type": "token_count", "rate_limits": limits}}, separators=(",", ":"))


class CodexUsage(HomeCase):
    def setUp(self):
        super().setUp()
        self.sessions = self.path(".codex", "sessions")
        p = mock.patch.object(codex, "CODEX_SESSIONS", self.sessions)
        p.start()
        self.addCleanup(p.stop)

    def rollout(self, name, lines, mtime):
        path = write(os.path.join(self.sessions, "2026", "10", "31", "rollout-%s.jsonl" % name),
                     "\n".join(lines) + "\n")
        os.utime(path, (mtime, mtime))
        return path

    def test_R_use_05_R_use_06_no_data(self):
        self.assertEqual(codex.Codex().usage(), {"error": "no Codex rate-limit data yet"})
        self.rollout("a", ['{"type":"event_msg","payload":{}}'], 1000)
        self.assertEqual(codex.Codex().usage(), {"error": "no Codex rate-limit data yet"})

    def test_R_use_04_R_use_05_R_use_06_bars(self):
        far = time.time() + 3600
        limits = {"primary": {"used_percent": 12.0, "window_minutes": 300, "resets_at": far},
                  "secondary": {"used_percent": 40.0, "window_minutes": 10080, "resets_at": 1000},
                  "plan_type": "plus"}
        self.rollout("old", [token_line("2026-10-30T00:00:00.000Z", {"primary": {"used_percent": 99.0}})], 1000)
        self.rollout("new", [token_line("2026-10-31T12:00:00.000Z", limits), '{"partial": '], 2000)
        got = codex.Codex().usage()
        self.assertEqual(got["plan"], "plus")
        self.assertEqual(got["observed_at"], 1793448000.0)
        self.assertEqual(got["bars"], [
            {"label": "session (5h)", "used": 12.0, "resets_at": far, "window": 18000, "stale": False},
            {"label": "week", "used": 0.0, "resets_at": 1000, "window": 604800, "stale": True}])

    def test_R_use_06_labels_and_order(self):
        limits = {"primary": {"used_percent": 1, "window_minutes": 10080},
                  "secondary": {"window_minutes": 0}}
        self.rollout("x", [token_line(None, limits)], 1500)
        got = codex.Codex().usage()
        self.assertEqual([b["label"] for b in got["bars"]], ["secondary", "week"])
        self.assertEqual(got["observed_at"], 1500)


if __name__ == "__main__":
    unittest.main()
