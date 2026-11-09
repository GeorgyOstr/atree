"""Adapters: parsing each agent's on-disk format, liveness, moves and the full scan."""
import json
import os
import sqlite3
import time
import unittest
from unittest import mock

from support import (HomeCase, TS, jsonl, make_all, make_claude, make_codex, read, tree_state, write)

from atree import ops, scan
from atree.agents import AGENTS, OpError, base, claude, codex, copilot, gemini, opencode


class Registry(unittest.TestCase):
    def test_r_base_1(self):
        got = [(a.name, a.label, a.color, a.can_move, a.can_archive) for a in AGENTS]
        self.assertEqual(got, [
            ("claude", "Claude", "#da724b", True, True), ("codex", "Codex", "#5b8cff", True, True),
            ("gemini", "Gemini", "#2f9e7a", False, True), ("opencode", "OpenCode", "#b06ccf", False, False),
            ("copilot", "Copilot", "#b08a14", False, True)])


class Scan(HomeCase):
    def test_r_e2e_2_one_talk_per_agent(self):
        make_all(self.home, self.project)
        talks = scan.scan_all()
        self.assertEqual(sorted(t["tool"] for t in talks), sorted(a.name for a in AGENTS))
        self.assertTrue(all(t["prompts"] == 1 for t in talks))
        self.assertTrue(all(t["cwd"] == self.project for t in talks))
        self.assertEqual(talks, scan.scan_all())

    def test_r_scan_1_failure_isolated(self):
        make_all(self.home, self.project)
        errors = []
        with mock.patch.object(claude.Claude, "scan", side_effect=RuntimeError("bad")):
            talks = scan.scan_all(errors)
        self.assertEqual(errors, ["scanning Claude failed: bad"])
        self.assertEqual(len(talks), 4)


class ClaudeTests(HomeCase):
    def registry(self, name, rec):
        write(self.path(".claude", "sessions", name + ".json"),
              rec if isinstance(rec, str) else json.dumps(rec))

    def test_r_claude_1_registry_validation(self):
        make_claude(self.home, self.project, sid="fixture")
        bad = [[], None, "x", 1, {}, {"sessionId": ["fixture"], "pid": os.getpid()}]
        bad += [{"sessionId": "fixture", "pid": p} for p in (None, "123", 1.5, True, False, 0, -1, 10**100)]
        for i, rec in enumerate(bad):
            self.registry("bad%d" % i, rec)
        self.registry("broken", "{")
        self.registry("good", {"sessionId": "fixture", "pid": os.getpid(), "status": "waiting"})
        self.assertEqual(claude.live_sessions(), {"fixture": {"status": "waiting"}})
        [t] = claude.Claude().scan()
        self.assertEqual((t["key"], t["live"], t["live_status"]), ("claude:fixture", True, "waiting"))

    def test_r_claude_2_bad_pids_never_probed(self):
        make_claude(self.home, self.project, sid="fixture")
        for i, pid in enumerate((0, -1, -123, True, False)):
            self.registry("r%d" % i, {"sessionId": "fixture", "pid": pid})
        with mock.patch.object(base, "pid_alive") as probe:
            [t] = claude.Claude().scan()
        probe.assert_not_called()
        self.assertFalse(t["live"])

    def test_parsing(self):
        uuid = lambda n: "00000000-0000-0000-0000-%012d" % n  # noqa: E731
        lines = [
            {"type": "user", "cwd": "/w", "timestamp": TS, "gitBranch": "main", "uuid": uuid(1),
             "parentUuid": uuid(99), "message": {"content": "first prompt"}},
            {"type": "user", "isMeta": True, "message": {"content": "meta"}},
            {"type": "user", "message": {"content": "<command>echo</command>"}},
            {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
            {"type": "assistant", "uuid": uuid(2), "message": {"id": "msg_a", "usage": {"input_tokens": 10, "output_tokens": 1}}},
            {"type": "assistant", "message": {"id": "msg_a", "usage": {"input_tokens": 10, "output_tokens": 5}}},
            {"type": "assistant", "message": {"id": "msg_b", "usage": {"input_tokens": 20, "cache_read_input_tokens": 3, "output_tokens": 2}}},
            {"type": "assistant", "isSidechain": True, "message": {"id": "msg_c", "usage": {"input_tokens": 1000}}},
            {"type": "ai-title", "aiTitle": "AI title"},
            {"type": "user", "timestamp": "2026-01-02T12:00:00Z", "message": {"content": "second"}},
        ]
        p = write(self.path(".claude", "projects", "-w", "s1.jsonl"),
                  "".join(json.dumps(l, separators=(",", ":")) + "\n" for l in lines))
        [t] = claude.Claude().scan()
        self.assertEqual(t["prompts"], 2)
        self.assertEqual(t["first_msg"], "first prompt")
        self.assertEqual(t["title"], "AI title")
        self.assertEqual(t["branch"], "main")
        self.assertEqual(t["context"], 25)
        self.assertEqual(t["tokens"], 15 + 25)
        self.assertEqual(t["path"], p)
        with open(p, "a") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": "Mine"}, separators=(",", ":")) + "\n")
        self.assertEqual(claude.Claude().scan()[0]["title"], "Mine")

    def test_g_core_9_malformed_lines_are_silent(self):
        good = {"type": "user", "cwd": "/w", "timestamp": TS, "message": {"content": "ok"}}
        p = write(self.path(".claude", "projects", "-w", "s1.jsonl"),
                  '{"type":"ai-title","aiTitle":\n{"type":"custom-title",broken\n{"type":"user",oops\n' + jsonl(good))
        with mock.patch("builtins.print") as out:
            [t] = claude.Claude().scan()
        out.assert_not_called()
        self.assertEqual((t["prompts"], t["first_msg"], t["path"]), (1, "ok", p))

    def test_empty_shell_skipped_and_continuation(self):
        write(self.path(".claude", "projects", "-w", "empty.jsonl"), jsonl({"type": "custom-title", "customTitle": "x"}))
        u1, u2 = "00000000-0000-0000-0000-000000000001", "00000000-0000-0000-0000-000000000002"
        sep = (",", ":")
        write(self.path(".claude", "projects", "-w", "a.jsonl"), json.dumps(
            {"type": "user", "cwd": "/w", "uuid": u1, "message": {"content": "a"}}, separators=sep) + "\n")
        write(self.path(".claude", "projects", "-w", "b.jsonl"), json.dumps(
            {"type": "user", "cwd": "/w", "uuid": u2, "parentUuid": u1, "message": {"content": "b"}}, separators=sep) + "\n")
        talks = {t["id"]: t for t in claude.Claude().scan()}
        self.assertEqual(set(talks), {"a", "b"})
        self.assertEqual(talks["b"]["related"], [{"key": "claude:a", "kind": "continued"}])
        self.assertEqual(talks["a"]["related"], [])

    def test_r_claude_3_usage_hints(self):
        settings = self.path(".claude", "settings.json")
        hint = {"error": "run atree.py --install-statusline"}
        with mock.patch("atree.statusline.limits", return_value=None, create=True):
            self.assertEqual(claude.Claude().usage(), hint)
            for text in ("{", "[]", json.dumps({"statusLine": "x"}),
                         json.dumps({"statusLine": {"command": "other --statusline"}})):
                write(settings, text)
                self.assertEqual(claude.Claude().usage(), hint, text)
            write(settings, json.dumps({"statusLine": {"type": "command", "command": "/opt/atree/atree.py --statusline"}}))
            self.assertEqual(claude.Claude().usage(), {"error": "waiting for Claude Code's status line"})

    def test_usage_bars(self):
        saved = {"at": 5, "rate_limits": {"five_hour": {"used_percentage": 40, "resets_at": time.time() + 99},
                                          "seven_day": {"used_percentage": 70, "resets_at": 1}}}
        with mock.patch("atree.statusline.limits", return_value=saved, create=True):
            u = claude.Claude().usage()
        self.assertEqual([(b["label"], b["used"], b["stale"]) for b in u["bars"]],
                         [("session (5h)", 40.0, False), ("week", 0.0, True)])
        self.assertEqual(u["observed_at"], 5)

    def companion(self, cwd, sid="abcdef12-session"):
        p = make_claude(self.home, cwd, sid=sid)
        write(os.path.join(p[:-6], "subagents", "agent.jsonl"), '{"cwd":"%s"}\n' % cwd)
        return p

    def test_r_claude_move_1_collisions(self):
        dest = self.path("destination")
        os.makedirs(dest)
        dst_dir = claude.project_dir(dest)
        for kind in ("file", "dir", "link", "link-companion"):
            with self.subTest(kind):
                src = self.companion(self.project)
                s = [t for t in claude.Claude().scan() if t["path"] == src][0]
                target = os.path.join(dst_dir, os.path.basename(src))
                companion = target[:-6]
                os.makedirs(dst_dir, exist_ok=True)
                if kind == "file":
                    write(target, "x")
                elif kind == "dir":
                    os.makedirs(companion)
                elif kind == "link":
                    os.symlink(self.path("nowhere"), target)
                else:
                    os.symlink(self.path("nowhere"), companion)
                before = tree_state(self.home)
                with self.assertRaisesRegex(OpError, "already exists"):
                    claude.Claude().move(s, dest)
                self.assertEqual(tree_state(self.home), before)
                for p in (target, companion):
                    if os.path.islink(p) or os.path.isfile(p):
                        os.unlink(p)
                    elif os.path.isdir(p):
                        os.rmdir(p)

    def test_r_claude_move_2_same_project_dir(self):
        src_cwd, dst_cwd = self.path("source-project"), self.path("source", "project")
        os.makedirs(dst_cwd)
        src = self.companion(src_cwd)
        original = read(src)
        [s] = claude.Claude().scan()
        claude.Claude().move(s, dst_cwd)
        self.assertIn('"cwd":"%s"' % dst_cwd, read(src, "r"))
        self.assertEqual(read(os.path.join(src[:-6], "subagents", "agent.jsonl"), "r"), '{"cwd":"%s"}\n' % src_cwd)
        [b] = os.listdir(base.BACKUPS)
        self.assertEqual(read(os.path.join(base.BACKUPS, b, os.path.basename(src))), original)

    def test_r_claude_move_3_other_dir(self):
        dest = self.path("destination")
        os.makedirs(dest)
        src = self.companion(self.project)
        [s] = claude.Claude().scan()
        claude.Claude().move(s, dest)
        new = os.path.join(claude.project_dir(dest), os.path.basename(src))
        self.assertIn('"cwd":"%s"' % dest, read(new, "r"))
        self.assertTrue(os.path.isfile(os.path.join(new[:-6], "subagents", "agent.jsonl")))
        self.assertFalse(os.path.exists(src) or os.path.exists(src[:-6]))

    def test_project_dir_too_long(self):
        with self.assertRaisesRegex(OpError, "too long"):
            claude.project_dir("/" + "a" * 250)


class CodexTests(HomeCase):
    def db(self, extra_tables=False):
        con = sqlite3.connect(codex.CODEX_DB)
        with con:
            con.execute("CREATE TABLE threads (id, created_at, updated_at, cwd, title, archived, archived_at,"
                        " git_branch, first_user_message, agent_nickname, agent_role, rollout_path,"
                        " thread_source, source)")
            con.execute("INSERT INTO threads (id, title, archived, cwd) VALUES ('visible', 'Saved title', 0, '/old')")
            con.execute("INSERT INTO threads (id, title, archived) VALUES ('archived', 'Gone', 1)")
            if extra_tables:
                con.execute("CREATE TABLE thread_spawn_edges (parent_thread_id, child_thread_id)")
                con.execute("INSERT INTO thread_spawn_edges VALUES ('indexed-parent', 'visible')")
        con.close()

    def test_r_codex_1_paths(self):
        import importlib
        for value, expect in ((None, os.path.join(self.home, ".codex")), ("", os.path.join(self.home, ".codex")),
                              (self.path("with space"), self.path("with space"))):
            env = {k: v for k, v in os.environ.items() if k != "CODEX_HOME"}
            if value is not None:
                env["CODEX_HOME"] = value
            with mock.patch.dict(os.environ, env, clear=True):
                mod = importlib.reload(codex)
                self.assertEqual((mod.CODEX_DIR, mod.CODEX_SESSIONS, mod.CODEX_DB),
                                 (expect, os.path.join(expect, "sessions"), os.path.join(expect, "state_5.sqlite")))
        importlib.reload(codex)

    def test_r_codex_2_and_3_index(self):
        os.makedirs(codex.CODEX_DIR, exist_ok=True)
        self.db()
        for sid in ("visible", "archived"):
            make_codex(self.home, "/w", sid=sid, meta={"parent_thread_id": "rollout-parent"})
        [t] = codex.Codex().scan()
        self.assertEqual((t["id"], t["title"], t["parent"]), ("visible", "Saved title", "codex:rollout-parent"))
        os.unlink(codex.CODEX_DB)
        self.db(extra_tables=True)
        self.assertEqual(codex.Codex().scan()[0]["parent"], "codex:indexed-parent")

    def test_g_core_12_last_spawn_edge_wins(self):
        os.makedirs(codex.CODEX_DIR, exist_ok=True)
        self.db(extra_tables=True)
        con = sqlite3.connect(codex.CODEX_DB)
        with con:
            con.execute("INSERT INTO thread_spawn_edges VALUES ('later-parent', 'visible')")
        con.close()
        self.assertEqual(codex._index()[1], {"visible": "later-parent"})

    def test_g_core_3_corrupt_first_line_marks_the_file_unknown(self):
        good = make_codex(self.home, "/w", sid="good")
        bad = self.path(".codex", "sessions", "2026", "01", "01", "rollout-bad.jsonl")
        with open(good) as f:
            write(bad, "{not json\n" + f.read().replace('"good"', '"bad"'))
        with mock.patch("builtins.print") as out:
            self.assertEqual([t["id"] for t in codex.Codex().scan()], ["good"])
            with open(bad, "a") as f:  # still unknown after more lines arrive
                f.write(jsonl({"timestamp": TS, "type": "event_msg", "payload": {"type": "task_started"}}))
            self.assertEqual([t["id"] for t in codex.Codex().scan()], ["good"])
        out.assert_not_called()

    def test_r_codex_4_git_shapes(self):
        make_codex(self.home, "/w", sid="listgit", meta={"git": ["x"]})
        make_codex(self.home, "/w", sid="okgit", meta={"git": {"branch": "main"}})
        talks = {t["id"]: t for t in codex.Codex().scan()}
        self.assertEqual((talks["listgit"]["branch"], talks["okgit"]["branch"]), (None, "main"))

    def test_tokens_and_fork(self):
        line = {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": 900}, "last_token_usage": {"total_tokens": 120}}}}
        make_codex(self.home, "/w", meta={"forked_from_id": "orig"}, extra=[line])
        [t] = codex.Codex().scan()
        self.assertEqual((t["context"], t["tokens"]), (120, 900))
        self.assertEqual(t["related"], [{"key": "codex:orig", "kind": "fork"}])

    def test_r_codex_5_move(self):
        os.makedirs(codex.CODEX_DIR, exist_ok=True)
        self.db()
        p = make_codex(self.home, "/old", sid="visible",
                       extra=[{"type": "turn_context", "payload": {"cwd": "/old/sub"}}])
        os.chmod(p, 0o640)
        mt = os.stat(p).st_mtime_ns
        target = self.path("new")
        os.makedirs(target)
        [s] = codex.Codex().scan()
        codex.Codex().move(s, target)
        text = read(p, "r")
        self.assertIn('"cwd":"%s"' % target, text)
        self.assertIn('"cwd":"%s/sub"' % target, text)
        self.assertNotIn("/old", text)
        self.assertEqual((os.stat(p).st_mode & 0o777, os.stat(p).st_mtime_ns), (0o640, mt))
        con = sqlite3.connect(codex.CODEX_DB)
        self.assertEqual(con.execute("SELECT cwd FROM threads WHERE id='visible'").fetchone()[0], target)
        con.close()

    def test_r_codex_6_readonly_index_rolls_back(self):
        os.makedirs(codex.CODEX_DIR, exist_ok=True)
        self.db()
        p = make_codex(self.home, "/old", sid="visible")
        os.chmod(p, 0o640)
        original = (read(p), os.stat(p).st_mode, os.stat(p).st_mtime_ns)
        os.chmod(codex.CODEX_DB, 0o444)
        [s] = codex.Codex().scan()
        with self.assertRaisesRegex(OpError, "stays in /old"):
            codex.Codex().move(s, self.project)
        self.assertEqual((read(p), os.stat(p).st_mode, os.stat(p).st_mtime_ns), original)
        con = sqlite3.connect(codex.CODEX_DB)
        self.assertEqual(con.execute("SELECT cwd FROM threads WHERE id='visible'").fetchone()[0], "/old")
        con.close()
        self.assertEqual(len(os.listdir(base.BACKUPS)), 1)

    def test_usage(self):
        reset = time.time() + 3600
        make_codex(self.home, "/w", extra=[{"timestamp": TS, "payload": {"rate_limits": {
            "primary": {"used_percent": 12.5, "window_minutes": 300, "resets_at": reset},
            "secondary": {"used_percent": 50, "window_minutes": 10080, "resets_at": reset},
            "plan_type": "plus"}}}])
        u = codex.Codex().usage()
        self.assertEqual([(b["label"], b["used"]) for b in u["bars"]], [("session (5h)", 12.5), ("week", 50.0)])
        self.assertEqual((u["plan"], u["observed_at"]), ("plus", 1767268800))


class GeminiTests(HomeCase):
    def chat(self, name, *records, project="p"):
        d = self.path(".gemini", "tmp", project)
        write(os.path.join(d, ".project_root"), "/w")
        return write(os.path.join(d, "chats", name), jsonl(*records))

    def test_records(self):
        self.chat("session-1.jsonl",
                  {"sessionId": "g1", "projectHash": "h", "startTime": TS},
                  {"id": "a", "type": "user", "timestamp": TS, "content": "/help"},
                  {"id": "b", "type": "user", "timestamp": TS, "content": [{"text": "real "}, {"text": "prompt"}]},
                  {"id": "c", "type": "gemini", "tokens": {"input": 5, "output": 2, "total": 9}},
                  {"id": "d", "type": "user", "content": "later"},
                  {"$rewindTo": "d"},
                  {"$patch": {"id": "b", "content": "edited"}},
                  {"$set": {"summary": "Summary title"}})
        [t] = gemini.Gemini().scan()
        self.assertEqual((t["prompts"], t["first_msg"], t["title"]), (1, "edited", "Summary title"))
        self.assertEqual((t["context"], t["tokens"]), (7, 9))

    def test_g_core_10_non_string_rewind_is_not_a_rewind(self):
        for target in (0, None, ["a"], {"x": 1}):
            self.chat("session-1.jsonl",
                      {"sessionId": "g1", "projectHash": "h"},
                      {"id": "a", "type": "user", "timestamp": TS, "content": "kept"},
                      {"$rewindTo": target, "$set": {"summary": "Set too"}})
            with mock.patch("builtins.print") as out:
                [t] = gemini.Gemini().scan()
            out.assert_not_called()
            self.assertEqual((t["prompts"], t["first_msg"], t["title"]), (1, "kept", "Set too"), target)

    def test_g_core_9_malformed_lines_are_silent(self):
        p = self.chat("session-1.jsonl", {"sessionId": "g1", "projectHash": "h"})
        with open(p, "a") as f:
            f.write("{broken\n" + jsonl({"id": "a", "type": "user", "content": "hi"}))
        with mock.patch("builtins.print") as out:
            [t] = gemini.Gemini().scan()
        out.assert_not_called()
        self.assertEqual(t["prompts"], 1)

    def test_subagent_and_dedupe(self):
        self.chat("session-sub.jsonl", {"sessionId": "s", "projectHash": "h", "kind": "subagent"},
                  {"id": "a", "type": "user", "content": "x"})
        old = self.path(".gemini", "tmp", "p", "chats", "session-g.json")
        write(old, json.dumps({"sessionId": "g", "messages": [{"id": "a", "type": "user", "content": "x"}]}))
        self.chat("session-g.jsonl", {"sessionId": "g", "projectHash": "h"}, {"id": "a", "type": "user", "content": "x"})
        [t] = gemini.Gemini().scan()
        self.assertTrue(t["path"].endswith(".jsonl"))

    def test_hash_lookup(self):
        import hashlib
        cwd = "/some/where"
        h = hashlib.sha256(cwd.encode()).hexdigest()
        write(self.path(".gemini", "projects.json"), json.dumps({"projects": {cwd: "slug"}}))
        write(self.path(".gemini", "tmp", h, "chats", "session-x.json"),
              json.dumps({"sessionId": "x", "messages": [{"id": "1", "type": "user", "content": "hi"}]}))
        [t] = gemini.Gemini().scan()
        self.assertEqual(t["cwd"], cwd)


class OpenCodeTests(HomeCase):
    def db(self, path, title, time_suspended=0, sid="s1", updated=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        con = sqlite3.connect(path)
        with con:
            con.execute("CREATE TABLE IF NOT EXISTS session_v2 (id, directory, title, time_created, time_updated, time_suspended)")
            con.execute("CREATE TABLE IF NOT EXISTS session_message (id, session_id, type, seq, time_created, data)")
            t = updated or int(time.time() * 1000)
            con.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?,?)", (sid, "/w", title, t, t, time_suspended))
            con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?)",
                        (sid + "m", sid, "user", 1, t, json.dumps({"text": "hi"})))
        con.close()

    def test_r_opencode_1_inode_change(self):
        self.db(opencode.DB, "first")
        self.assertEqual(opencode.OpenCode().scan()[0]["title"], "first")
        st = os.stat(opencode.DB)
        other = opencode.DB + ".other"
        self.db(other, "secnd")
        os.truncate(other, st.st_size) if os.path.getsize(other) > st.st_size else None
        os.utime(other, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(other, opencode.DB)
        self.assertEqual(opencode.OpenCode().scan()[0]["title"], "secnd")
        self.assertIs(opencode.rows(), opencode.rows())

    def test_r_opencode_2_dead_server(self):
        hour_ago = int((time.time() - 3600) * 1000)
        self.db(opencode.DB, "busy", time_suspended=1, sid="a", updated=hour_ago)
        self.db(opencode.DB, "idle", sid="b", updated=hour_ago)
        write(opencode.SERVICE, json.dumps({"pid": 10**100}))
        talks = opencode.OpenCode().scan()
        self.assertEqual(len(talks), 2)
        self.assertEqual([(t["live"], t["live_status"]) for t in talks], [(False, None)] * 2)

    def test_uri_special_characters_in_paths(self):
        # '#', '?' and '%' must not truncate the read-only URI (which would create a stray database)
        folder = self.path("odd#dir%41?x")
        db = os.path.join(folder, "opencode.db")
        self.db(db, "odd")
        before = sorted(os.listdir(self.home))
        with mock.patch.object(opencode, "DB", db):
            self.assertEqual([t["title"] for t in opencode.OpenCode().scan()], ["odd"])
        self.assertEqual(sorted(os.listdir(self.home)), before)
        cdb = os.path.join(folder, "state_5.sqlite")
        con = sqlite3.connect(cdb)
        with con:
            con.execute("CREATE TABLE threads (id, title)")
            con.execute("INSERT INTO threads VALUES ('t', 'Saved')")
        con.close()
        with mock.patch.object(codex, "CODEX_DB", cdb):
            self.assertEqual(codex._index()[0], {"t": {"id": "t", "title": "Saved"}})
        self.assertEqual(sorted(os.listdir(self.home)), before)

    def test_v1_parts(self):
        os.makedirs(os.path.dirname(opencode.DB), exist_ok=True)
        con = sqlite3.connect(opencode.DB)
        with con:
            con.execute("CREATE TABLE session (id, directory, title, time_created, time_updated)")
            con.execute("CREATE TABLE message (id, session_id, time_created, data)")
            con.execute("CREATE TABLE part (id, message_id, data)")
            con.execute("INSERT INTO session VALUES ('s', '/w', '', 1000, 1000)")
            con.execute("INSERT INTO message VALUES ('m', 's', 1000, ?)", (json.dumps({"role": "user"}),))
            con.execute("INSERT INTO part VALUES ('p1', 'm', ?)", (json.dumps({"type": "text", "text": "syn", "synthetic": True}),))
            con.execute("INSERT INTO part VALUES ('p2', 'm', ?)", (json.dumps({"type": "text", "text": " real "}),))
        con.close()
        [t] = opencode.OpenCode().scan()
        self.assertEqual((t["first_msg"], t["title"]), ("real", "real"))

    def test_g_core_14_message_text_is_not_stripped(self):
        self.db(opencode.DB, "")
        con = sqlite3.connect(opencode.DB)
        with con:
            con.execute("UPDATE session_message SET data = ?", (json.dumps({"text": " " * 10 + "x" * 100}),))
        con.close()
        [t] = opencode.OpenCode().scan()
        # the 80-character title cut counts the leading blanks
        self.assertEqual((t["first_msg"], t["title"]), (" " * 10 + "x" * 100, "x" * 70))


class CopilotTests(HomeCase):
    def test_yaml(self):
        text = "id: abc\n# comment\nname: 'it''s'\nsummary: |\n  line one\n  line two\ncwd: \"/w\\u00e9\"\nbranch: ~\n"
        self.assertEqual(copilot.parse_yaml(text),
                         {"id": "abc", "name": "it's", "summary": "line one\nline two", "cwd": "/wé"})

    def test_g_core_13_null_repeat_keeps_earlier_value(self):
        text = "name: first\nname: ~\nbranch: main\nbranch: null\ncwd: /a\ncwd:\nid: x\nid: y\n"
        self.assertEqual(copilot.parse_yaml(text), {"name": "first", "branch": "main", "cwd": "/a", "id": "y"})

    def test_g_core_9_malformed_lines_are_silent(self):
        d = self.path(".copilot", "session-state", "s")
        write(os.path.join(d, "events.jsonl"), '{"type":"user.message",oops\n{"type":"session.start"\n'
              + jsonl({"type": "user.message", "timestamp": TS, "data": {"content": "x"}}))
        with mock.patch("builtins.print") as out:
            [t] = copilot.Copilot().scan()
        out.assert_not_called()
        self.assertEqual(t["prompts"], 1)

    def test_r_copilot_1_huge_pid_lock(self):
        for sid in ("a", "b"):
            d = self.path(".copilot", "session-state", sid)
            write(os.path.join(d, "events.jsonl"), jsonl(
                {"type": "user.message", "timestamp": TS, "data": {"content": "hi"}}))
        write(self.path(".copilot", "session-state", "a", "inuse.%d.lock" % 10**100), "")
        with mock.patch.object(copilot, "_start_time") as start:
            talks = copilot.Copilot().scan()
        start.assert_not_called()
        self.assertEqual([t["live"] for t in talks], [False, False])

    def test_events(self):
        d = self.path(".copilot", "session-state", "s")
        write(os.path.join(d, "events.jsonl"), jsonl(
            {"type": "session.start", "timestamp": TS, "data": {"context": {"cwd": "/a", "branch": "b1"}}},
            {"type": "user.message", "timestamp": TS, "agentId": "sub", "data": {"content": "sub"}},
            {"type": "user.message", "timestamp": TS, "data": {"content": "auto", "source": "system"}},
            {"type": "user.message", "timestamp": TS, "data": {"content": " mine "}},
            {"type": "session.context_changed", "data": {"cwd": "/b"}},
            {"type": "session.shutdown", "data": {"modelMetrics": {"m": {"usage": {"inputTokens": 3, "outputTokens": 4}}}}}))
        [t] = copilot.Copilot().scan()
        self.assertEqual((t["prompts"], t["first_msg"], t["cwd"], t["branch"], t["tokens"]), (1, "mine", "/b", "b1", 7))

    def test_live_lock(self):
        d = self.path(".copilot", "session-state", "s")
        write(os.path.join(d, "events.jsonl"), jsonl({"type": "user.message", "data": {"content": "x"}}))
        write(os.path.join(d, "inuse.%d.lock" % os.getpid()), "", mtime=None)
        self.assertTrue(copilot.Copilot().scan()[0]["live"])


class MoveOp(HomeCase):
    def test_r_move_1(self):
        make_all(self.home, self.project)
        os.makedirs(codex.CODEX_DIR, exist_ok=True)
        target = self.path("elsewhere")
        os.makedirs(target)
        for key in ("claude:claude-fixture", "codex:codex-fixture"):
            self.assertEqual(ops.move(key, target), target)
        cwds = {t["key"]: t["cwd"] for t in scan.scan_all()}
        self.assertEqual(cwds["claude:claude-fixture"], target)
        self.assertEqual(cwds["codex:codex-fixture"], target)
        self.assertEqual(ops.move("codex:codex-fixture", target), "unchanged")

    def test_move_errors(self):
        make_all(self.home, self.project)
        with self.assertRaisesRegex(OpError, "is not a directory"):
            ops.move("claude:claude-fixture", self.path("missing"))
        with self.assertRaisesRegex(OpError, "moving Gemini talks isn't supported"):
            ops.move("gemini:gemini-fixture", self.home)
        with self.assertRaisesRegex(OpError, "session claude:nope not found"):
            ops.move("claude:nope", self.home)


class TalkShape(unittest.TestCase):
    def test_non_string_fields_are_coerced(self):
        t = base.talk("codex", "abcdef123", cwd=5, title=None, first_msg=["x"], branch=7)
        self.assertEqual((t["cwd"], t["title"], t["first_msg"], t["branch"]), ("/", "abcdef12", "", "7"))


if __name__ == "__main__":
    unittest.main()
