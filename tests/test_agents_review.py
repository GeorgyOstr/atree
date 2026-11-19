"""Adapter regressions: appends, prompt blocks, liveness, index checks, usage and backups."""
import json
import os
import sqlite3
import time
import unittest
from unittest import mock

from support import HomeCase, TS, jsonl, make_codex, write

from atree.agents import OpError, base, claude, codex, gemini, opencode

SEP = (",", ":")


def line(rec):
    return json.dumps(rec, separators=SEP) + "\n"


class ClaudeReview(HomeCase):
    def test_rename_appends_and_terminates_a_partial_line(self):
        p = write(self.path(".claude", "projects", "-w", "s.jsonl"),
                  line({"type": "user", "cwd": "/w", "timestamp": TS, "message": {"content": "hi"}}) + '{"partial')
        [s] = claude.Claude().scan()
        real_open = open
        modes = []

        def spy(path, mode="r", *a, **k):
            modes.append(mode)
            return real_open(path, mode, *a, **k)

        with mock.patch("builtins.open", spy):
            claude.Claude().rename(s, "New")
        self.assertIn("ab", modes)
        self.assertNotIn("rb+", modes)
        with open(p) as f:
            self.assertTrue(f.read().endswith('{"partial\n' + line(
                {"type": "custom-title", "customTitle": "New", "sessionId": "s"})))

    def test_prompt_blocks(self):
        msgs = [
            [{"type": "text", "text": "<ide_selection>x</ide_selection>"}, {"type": "text", "text": "fix it"}],
            [{"type": "text", "text": ""}, {"type": "text", "text": "second block"}],
            [{"type": "text", "text": "part one"}, {"type": "image"}, {"type": "text", "text": "part two"}],
            [{"type": "text", "text": "<command-name>/clear</command-name>"}],
        ]
        p = write(self.path(".claude", "projects", "-w", "s.jsonl"), "".join(
            line({"type": "user", "cwd": "/w", "timestamp": TS, "message": {"content": m}}) for m in msgs))
        [t] = claude.Claude().scan()
        self.assertEqual((t["prompts"], t["first_msg"]), (3, "fix it"))
        self.assertEqual(claude.Claude().transcript({"path": p}),
                         [("user", "fix it"), ("user", "second block"), ("user", "part one\npart two")])

    def test_continued_points_at_the_earliest_owner(self):
        u1, u2 = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
        rec = {"type": "user", "cwd": "/w", "uuid": u1, "message": {"content": "a"}}
        # 'z' is the original, 'a' a later fork that copied its message
        write(self.path(".claude", "projects", "-w", "z.jsonl"), line(dict(rec, timestamp="2026-01-01T00:00:00Z")))
        write(self.path(".claude", "projects", "-w", "a.jsonl"), line(dict(rec, timestamp="2026-01-02T00:00:00Z")))
        write(self.path(".claude", "projects", "-w", "next.jsonl"), line(
            {"type": "user", "cwd": "/w", "uuid": u2, "parentUuid": u1, "timestamp": "2026-01-03T00:00:00Z",
             "message": {"content": "b"}}))
        talks = {t["id"]: t for t in claude.Claude().scan()}
        self.assertEqual(talks["next"]["related"], [{"key": "claude:z", "kind": "continued"}])


class CodexReview(HomeCase):
    def db(self):
        os.makedirs(codex.CODEX_DIR, exist_ok=True)
        con = sqlite3.connect(codex.CODEX_DB)
        with con:
            con.execute("CREATE TABLE threads (id, title, archived, cwd)")
            con.execute("INSERT INTO threads VALUES ('known', 'Old', 0, '/w')")
        con.close()

    def test_rename_without_an_index_entry_fails(self):
        make_codex(self.home, "/w", sid="stray")
        [s] = codex.Codex().scan()
        with self.assertRaisesRegex(OpError, "no entry"):
            codex.Codex().rename(s, "x")  # no database at all
        self.db()
        with self.assertRaisesRegex(OpError, "no entry"):
            codex.Codex().rename(s, "x")
        make_codex(self.home, "/w", sid="known")
        known = next(t for t in codex.Codex().scan() if t["id"] == "known")
        codex.Codex().rename(known, "New")
        self.assertEqual(next(t for t in codex.Codex().scan() if t["id"] == "known")["title"], "New")

    def test_future_mtime_is_not_live(self):
        p = make_codex(self.home, "/w", sid="f", )
        future = time.time() + 86400
        os.utime(p, (future, future))
        self.assertFalse(codex.Codex().scan()[0]["live"])
        now = time.time() + 5  # a small step back of the clock still reads as live
        os.utime(p, (now, now))
        base._lines_cache.clear()
        self.assertTrue(codex.Codex().scan()[0]["live"])

    def test_tail_reader_crosses_chunks(self):
        rl = {"primary": {"used_percent": 7}}
        rec = {"timestamp": TS, "payload": {"rate_limits": rl, "pad": "x" * 300}}
        p = write(self.path("r.jsonl"), line(rec) + "".join(line({"n": i}) for i in range(50)))
        self.assertEqual(codex._tail_rate_limits(p, chunk=37), (rl, 1767268800))
        self.assertEqual(codex._tail_rate_limits(p, limit=100, chunk=37), (None, None))
        self.assertEqual(codex._tail_rate_limits(write(self.path("e.jsonl"), "")), (None, None))

    def test_usage_takes_the_newest_record(self):
        reset = time.time() + 3600
        limits = lambda pct: {"primary": {"used_percent": pct, "window_minutes": 300, "resets_at": reset}}  # noqa: E731
        newer = make_codex(self.home, "/w", sid="old-resumed", extra=[
            {"timestamp": "2026-01-01T00:00:00Z", "payload": {"rate_limits": limits(10)}}])
        other = make_codex(self.home, "/w", sid="recent", extra=[
            {"timestamp": "2026-01-05T00:00:00Z", "payload": {"rate_limits": limits(60)}}])
        os.utime(newer, (1767700000, 1767700000))  # touched last, but holds the older record
        os.utime(other, (1767600000, 1767600000))
        u = codex.Codex().usage()
        self.assertEqual(u["bars"][0]["used"], 60.0)

    def test_index_is_cached_until_the_database_changes(self):
        self.db()
        first = codex._index()
        self.assertIs(codex._index(), first)
        con = sqlite3.connect(codex.CODEX_DB)
        with con:
            con.execute("UPDATE threads SET title = 'Changed, longer title'")
        con.close()
        self.assertEqual(codex._index()[0]["known"]["title"], "Changed, longer title")


class GeminiReview(HomeCase):
    @unittest.skipIf(os.geteuid() == 0, "root reads any directory")
    def test_unreadable_project_does_not_hide_others(self):
        for proj in ("a", "b"):
            d = self.path(".gemini", "tmp", proj)
            write(os.path.join(d, ".project_root"), "/w")
            write(os.path.join(d, "chats", "session-%s.jsonl" % proj), jsonl(
                {"sessionId": proj, "projectHash": proj, "startTime": TS},
                {"id": "m", "type": "user", "timestamp": TS, "content": "hello"}))
        locked = self.path(".gemini", "tmp", "a", "chats")
        os.chmod(locked, 0)
        self.addCleanup(os.chmod, locked, 0o755)
        self.assertEqual([t["id"] for t in gemini.Gemini().scan()], ["b"])


class OpenCodeReview(HomeCase):
    def test_bad_times_do_not_abort_the_scan(self):
        os.makedirs(os.path.dirname(opencode.DB), exist_ok=True)
        con = sqlite3.connect(opencode.DB)
        with con:
            con.execute("CREATE TABLE session_v2 (id, directory, title, time_created, time_updated)")
            con.execute("CREATE TABLE session_message (id, session_id, type, seq, time_created, data)")
            for sid, t in (("inf", float("inf")), ("huge", 9 * 10**18), ("far", 1e30), ("text", "soon"), ("ok", 1767268800000)):
                con.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?)", (sid, "/w", sid, t, t))
                con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?)",
                            (sid + "m", sid, "user", 1, t, json.dumps({"text": "hi"})))
        con.close()
        talks = {t["id"]: t for t in opencode.OpenCode().scan()}
        self.assertEqual(set(talks), {"inf", "huge", "far", "text", "ok"})
        self.assertEqual(talks["inf"]["days"], {})
        self.assertEqual(talks["ok"]["updated"], 1767268800)


class BackupReview(HomeCase):
    def test_same_second_backups_do_not_collide(self):
        p = write(self.path("t.jsonl"), "one\n")
        with mock.patch.object(base.time, "strftime", return_value="20260101-000000"):
            first = base.backup([p], "move-x")
            write(p, "two\n")
            second = base.backup([p], "move-x")
        self.assertNotEqual(first, second)
        with open(os.path.join(first, "t.jsonl")) as f:
            self.assertEqual(f.read(), "one\n")


if __name__ == "__main__":
    unittest.main()
