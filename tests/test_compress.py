"""Compressing a folder: grouping, the merged talk, archiving its sources, undo."""
import io
import json
import os
import sqlite3
from contextlib import redirect_stdout
from unittest import mock

from support import HomeCase, MTIME, TS, jsonl, make_claude, make_codex, make_gemini, read, tree_state, write
from atree import aisearch, assistant, compress, ops
from atree.agents import BY_NAME, OpError, claude, codex
from atree.scan import scan_all


def said(text):
    return {"type": "assistant", "timestamp": TS, "message": {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "hm"}, {"type": "text", "text": text},
        {"type": "tool_use", "name": "Bash", "input": {}}]}}


def codex_msg(role, text):
    kind = "output_text" if role == "assistant" else "input_text"
    return {"timestamp": TS, "type": "response_item",
            "payload": {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}}


class Transcripts(HomeCase):
    def test_claude(self):
        make_claude(self.home, self.project, "a", "fix the login", extra=[
            said("looking"),
            {"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x"}]}},
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "meta"}},
            {"type": "user", "message": {"role": "user", "content": "<command-name>/clear</command-name>"}},
            dict(said("a subagent"), isSidechain=True),
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use"}]}},
            {"type": "user", "timestamp": TS, "message": {"role": "user", "content": "now the logout"}},
            said("both done"), "not a record"])
        [s] = scan_all()
        self.assertEqual(BY_NAME["claude"].transcript(s), [
            ("user", "fix the login"), ("agent", "looking"), ("user", "now the logout"), ("agent", "both done")])
        self.assertEqual(s["prompts"], 2)  # the scan counts the same prompts

    def test_codex(self):
        make_codex(self.home, self.project, "c", extra=[
            codex_msg("developer", "You are Codex"),
            codex_msg("user", "<environment_context>\n<cwd>/p</cwd>"),
            codex_msg("user", "speed it up"),
            {"type": "response_item", "payload": {"type": "reasoning", "summary": []}},
            codex_msg("assistant", "measuring"), codex_msg("assistant", " "), codex_msg("assistant", "twice as fast")])
        [s] = scan_all()
        self.assertEqual(BY_NAME["codex"].transcript(s), [
            ("user", "speed it up"), ("agent", "measuring"), ("agent", "twice as fast")])

    def test_who_can(self):
        self.assertEqual(sorted(a.name for a in BY_NAME.values() if a.can_merge), ["claude", "codex"])
        self.assertEqual(sorted(a.name for a in BY_NAME.values() if compress.can_compress(a)), ["claude"])
        self.assertEqual(BY_NAME["codex"].merged_id("x", "model: m\nsession id: 01a-b2\n--------\n"), "01a-b2")
        self.assertIsNone(BY_NAME["codex"].merged_id("x", "no banner"))
        self.assertEqual(BY_NAME["claude"].merged_id("x", ""), "x")


class Pieces(HomeCase):
    def test_candidates_are_the_folders_own_idle_talks(self):
        sub = os.path.join(self.project, "sub")
        make_claude(self.home, self.project, "a")
        make_claude(self.home, self.project + "/", "b")
        make_claude(self.home, sub, "below")
        make_codex(self.home, self.project, "c")
        make_codex(self.home, self.project, "kid", meta={"parent_thread_id": "c"})
        make_gemini(self.home, self.project)
        talks = scan_all()
        self.assertEqual(len(talks), 6)
        got = lambda: sorted(s["key"] for s in compress.candidates(talks, self.project))  # noqa: E731
        self.assertEqual(got(), ["claude:a", "claude:b", "codex:c"])
        talks[[s["key"] for s in talks].index("claude:a")]["live"] = True
        talks[[s["key"] for s in talks].index("claude:b")]["archived"] = True
        self.assertEqual(got(), ["codex:c"])
        self.assertEqual([s["key"] for s in compress.candidates(talks, sub)], ["claude:below"])

    def test_groups_from_the_answer(self):
        pool = [dict(key="claude:%s" % k, title="talk " + k, created=n) for n, k in enumerate("abcdefg")]
        answer = 'Here: [{"title": " Login\\n fix ", "keys": ["claude:c", "claude:a", "claude:zz", "claude:a"], ' \
                 '"why": 7}, {"keys": ["claude:a", "claude:b"]}, {"title": 5, "keys": ["claude:d", "claude:e"]}, ' \
                 '{"keys": "claude:f"}, {"title": "x"}]'
        groups = compress.parse_groups(answer, pool)
        self.assertEqual([(g["title"], [s["key"] for s in g["talks"]], g["why"]) for g in groups], [
            ("Login fix", ["claude:a", "claude:c"], "7"),
            ("talk e", ["claude:d", "claude:e"], "")])  # b alone is no group; a title falls back
        self.assertIsNone(compress.parse_groups("no list here", pool))
        self.assertEqual(compress.parse_groups("[]", pool), [])
        many = json.dumps([{"keys": ["k%d" % i, "j%d" % i]} for i in range(9)])
        pool = [dict(key=p + str(i), title="t", created=0) for i in range(9) for p in "kj"]
        self.assertEqual(len(compress.parse_groups(many, pool)), compress.MAX_GROUPS)

    def test_long_messages_and_talks_are_cut(self):
        self.assertEqual(compress._clip(" short ", 10), "short")
        cut = compress._clip("a" * 60 + "b" * 60, 30)
        self.assertEqual((cut[:20], cut[-10:]), ("a" * 20, "b" * 10))
        self.assertIn("[… 90 characters left out …]", cut)
        parts = ["%02d" % i + "x" * 98 for i in range(30)]
        fit = compress._fit(parts, 1000)
        self.assertEqual([p[:2] for p in fit[:3]], ["00", "01", "02"])
        self.assertEqual(fit[3], "[… 20 messages left out …]")
        self.assertEqual([p[:2] for p in fit[4:]], ["23", "24", "25", "26", "27", "28", "29"])
        self.assertIs(compress._fit(parts, 3000), parts)

    def test_report_reads_the_answers_opening(self):
        r = compress.report("Sure.\n\n**Title:** “Merged talk: Faster `scan`.”\n**Brief:** Two talks\n"
                            "made it faster.\n\n### Lessons:\n\n1. Measure first\n   with perf\n* **none**\n"
                            "- " + "x" * 300 + "\n\n## 1. Goal\n- not a lesson\nTitle: later\n")
        self.assertEqual((r["title"], r["brief"]), ("Faster scan", "Two talks made it faster."))
        self.assertEqual(r["lessons"], ["Measure first with perf", "x" * 239 + "…"])
        r = compress.report("Title: T\nBrief: B\nLessons: none\n\n1. Goal\n- g\n")
        self.assertEqual(r, {"title": "T", "brief": "B", "lessons": []})
        r = compress.report("Title: T\nLessons:\n" + "- l\n" * 9 + "\n1. Goal: g\n")
        self.assertEqual((r["brief"], r["lessons"]), ("", ["l"] * 5))
        self.assertEqual(compress.report("Lessons: just one\n- no more")["lessons"], ["just one"])
        self.assertEqual(compress.report("\n" * compress.REPORT_LINES + "Title: too far down"),
                         {"title": "", "brief": "", "lessons": []})

    def test_digest_keeps_more_of_the_last_answer(self):
        make_claude(self.home, self.project, "a", "p" * 5000, extra=[said("m" * 5000), said("z" * 5000)])
        [s] = scan_all()
        user, mid, last = compress.digest(s, 40000).split("\n\n")
        self.assertTrue(user.startswith("USER: ppp") and mid.startswith("AGENT: mmm"))
        self.assertLess(len(user), compress.PROMPT_CAP + 60)
        self.assertLess(len(mid), compress.ANSWER_CAP + 60)
        self.assertEqual(last, "AGENT: " + "z" * 5000)


class Run(HomeCase):
    """compress() against a stand-in for the agent CLI."""

    def setUp(self):
        super().setUp()
        self.paths = {
            "claude:a": make_claude(self.home, self.project, "a", "fix the login", extra=[said("login fixed")]),
            "claude:b": make_claude(self.home, self.project, "b", "login breaks again", extra=[said("found it")]),
            "codex:c": make_codex(self.home, self.project, "c", extra=[
                codex_msg("user", "test the login"), codex_msg("assistant", "3 tests pass")]),
            "claude:d": make_claude(self.home, self.project, "d", "pancakes?", extra=[said("flour")]),
        }
        self.groups = [{"title": "Login fix", "keys": ["claude:a", "codex:c", "claude:b"], "why": "same bug"}]
        self.asked = []
        self.keeps = True
        self.answer = ("Title: **Login cookie fix**\nBrief: The login broke twice; the cookie path was wrong\n"
                       "and is fixed, with 3 tests.\n\nLessons:\n- Clear `cookies` before testing.\n"
                       "- Next: add a logout test.\n\n## 1. Goal\nTHE HAND-OVER\n")
        patch = mock.patch.object(aisearch, "ask", side_effect=self.ask)
        patch.start()
        self.addCleanup(patch.stop)

    def ask(self, a, argv, prompt, cwd=None, timeout=None):
        self.asked.append((argv, prompt, cwd))
        if "--session-id" not in argv:
            return json.dumps(self.groups), ""
        if self.keeps:  # what the CLI does: keep the session in the folder it ran in
            make_claude(self.home, cwd, argv[argv.index("--session-id") + 1], prompt, extra=[said(self.answer)])
        return self.answer, ""

    def keys(self):
        return sorted(s["key"] for s in scan_all())

    def test_merges_archives_and_undoes(self):
        # what is in each transcript folder (restoring keeps the content, not group-write bits)
        state = lambda p: {n: v[:2] for n, v in tree_state(os.path.dirname(p)).items()}  # noqa: E731
        before = {k: state(p) for k, p in self.paths.items()}
        notes = []
        r = compress.compress("claude", self.project, note=notes.append)
        self.assertEqual(r["errors"], [])
        [m] = r["merged"]
        self.assertEqual((m["title"], m["from"]), ("Login cookie fix", ["claude:a", "claude:b", "codex:c"]))
        self.assertEqual(self.keys(), sorted(["claude:d", m["key"]]))
        # the report: what went in, the agent's brief and lessons; the talk carries the agent's title
        self.assertEqual(m["sources"], ["fix the login", "login breaks again", "c"])
        self.assertEqual(m["brief"], "The login broke twice; the cookie path was wrong and is fixed, with 3 tests.")
        self.assertEqual(m["lessons"], ["Clear cookies before testing.", "Next: add a logout test."])
        self.assertEqual(m["name"], "Merged 3: Login cookie fix")
        saved = compress.last_report()      # kept for the window, whoever ran the compression
        self.assertEqual((saved["agent"], saved["path"], saved["talks"], saved["errors"]), ("Claude", self.project, 3, []))
        self.assertEqual(saved["items"], [{k: m[k] for k in ("key", "title", "brief", "lessons", "sources")}
                                          | {"tool": "claude"}])
        compress.forget_report(self.home)   # another folder's: stays
        self.assertIsNotNone(compress.last_report())
        self.assertEqual(next(s["title"] for s in scan_all() if s["key"] == m["key"]), m["name"])
        self.assertEqual(len(notes), 2)
        # the grouping saw every talk of the folder; the merge ran in the folder, on the three
        (_argv, listing, where), (argv, prompt, cwd) = self.asked
        self.assertIsNone(where)
        self.assertEqual(sum(k in listing for k in self.paths), 4)
        self.assertEqual(cwd, self.project)
        self.assertEqual(argv[argv.index("--name") + 1], "Merged: Login fix")
        self.assertTrue(prompt.startswith("Merged talk: Login fix\n\nThis talk replaces 3 earlier talks"))
        for text in ("USER: fix the login", "AGENT: login fixed", "USER: login breaks again",
                     "USER: test the login", "AGENT: 3 tests pass", "==== Talk 3 of 3"):
            self.assertIn(text, prompt)
        self.assertNotIn("pancakes", prompt)
        index = ops.archived()
        self.assertEqual(sorted(index), ["claude:a", "claude:b", "codex:c"])
        self.assertEqual({e["merged_into"] for e in index.values()}, {m["key"]})
        merged = [dict(e, key=k, archived=True) for k, e in index.items()]
        at = index["claude:a"]["merged_at"]
        self.assertEqual(compress.undoable(merged, at + 5), {self.project})
        self.assertEqual(compress.undoable(merged, at + compress.UNDO_WINDOW), set())

        u = compress.undo(self.project)
        self.assertEqual((sorted(u["restored"]), u["dropped"], u["errors"]),
                         (["claude:a", "claude:b", "codex:c"], [m["key"]], []))
        self.assertEqual(self.keys(), ["claude:a", "claude:b", "claude:d", "codex:c"])
        self.assertEqual(ops.archived(), {})
        self.assertIsNone(compress.last_report())
        for junk in ("{", "[]", '{"at": 1, "path": "/p", "items": []}', '{"at": "1", "path": "/p", "items": [{"key": "k"}]}'):
            write(compress._report_file(), junk)
            self.assertIsNone(compress.last_report())
        compress.forget_report()
        self.assertFalse(os.path.exists(compress._report_file()))
        self.assertEqual(os.listdir(ops.ARCHIVE_DIR), ["index.json"])
        for k, p in self.paths.items():
            self.assertEqual(state(p), before[k], k)
        with self.assertRaisesRegex(OpError, "no compression to undo"):
            compress.undo(self.project)

    def test_nothing_is_archived_unless_the_talk_was_kept(self):
        self.keeps = False
        r = compress.compress("claude", self.project)
        self.assertEqual(r["merged"], [])
        self.assertEqual(r["errors"], ["'Login fix': Claude answered but kept no talk; nothing was archived"])
        self.assertEqual(len(self.keys()), 4)
        self.assertEqual(ops.archived(), {})

    def test_a_talk_that_changed_meanwhile_stays(self):
        real = self.ask

        def ask(a, argv, prompt, cwd=None, timeout=None):
            if "--session-id" in argv:  # the user goes on with talk b while the agent writes
                with open(self.paths["claude:b"], "a") as f:
                    f.write(jsonl({"type": "user", "timestamp": TS, "message": {"role": "user", "content": "more"}}))
            return real(a, argv, prompt, cwd, timeout)

        aisearch.ask.side_effect = ask
        r = compress.compress("claude", self.project)
        self.assertEqual(r["merged"][0]["from"], ["claude:a", "codex:c"])
        self.assertEqual(r["errors"], ["'login breaks again' changed meanwhile and stays where it is"])
        self.assertIn("claude:b", self.keys())

    def test_undo_keeps_a_continued_talk_and_expires(self):
        r = compress.compress("claude", self.project)
        key = r["merged"][0]["key"]
        path = next(s["path"] for s in scan_all() if s["key"] == key)
        with open(path, "a") as f:
            f.write(jsonl({"type": "user", "timestamp": TS, "message": {"role": "user", "content": "go on"}}))
        at = ops.archived()["claude:a"]["merged_at"]
        with self.assertRaisesRegex(OpError, "no compression to undo"):
            compress.undo(self.project, now=at + compress.UNDO_WINDOW + 1)
        u = compress.undo(self.project, now=at + 60)
        self.assertEqual((len(u["restored"]), u["dropped"]), (3, []))
        self.assertEqual(u["errors"], ["kept 'Merged 3: Login cookie fix': it has been continued"])
        self.assertIn(key, self.keys())

    def test_a_merged_talk_nothing_went_into_is_taken_back(self):
        real = self.ask

        def ask(a, argv, prompt, cwd=None, timeout=None):
            if "--session-id" in argv:  # every talk goes on while the agent writes
                for k in ("claude:a", "claude:b"):
                    with open(self.paths[k], "a") as f:
                        f.write(jsonl({"type": "user", "timestamp": TS, "message": {"role": "user", "content": "more"}}))
                with open(self.paths["codex:c"], "a") as f:
                    f.write(jsonl(codex_msg("user", "more")))
            return real(a, argv, prompt, cwd, timeout)

        aisearch.ask.side_effect = ask
        r = compress.compress("claude", self.project)
        self.assertEqual(r["merged"], [])
        [err] = r["errors"]
        self.assertIn("none of its talks could be archived; nothing was merged", err)
        self.assertEqual(self.keys(), sorted(self.paths))
        self.assertEqual(ops.archived(), {})
        self.assertIsNone(compress.last_report())

    def test_a_merged_talk_continued_meanwhile_is_kept_even_if_nothing_went_in(self):
        real = ops.archive

        def archive(key, **kw):  # every source fails; the merged talk itself could go
            if key in self.paths:
                raise OpError("busy")
            return real(key, **kw)

        with mock.patch.object(ops, "archive", side_effect=archive), \
                mock.patch.object(BY_NAME["claude"], "live_pid", return_value=4242):
            r = compress.compress("claude", self.project)
        self.assertEqual(r["merged"], [])
        self.assertIn("it has been continued", r["errors"][0])
        self.assertEqual(len(self.keys()), len(self.paths) + 1)

    def test_a_source_archived_but_not_fully_removed_still_counts(self):
        real = ops.archive

        def archive(key, **kw):
            real(key, **kw)
            raise OpError("archived %s, but removing the originals failed: denied" % key)

        with mock.patch.object(ops, "archive", side_effect=archive):
            r = compress.compress("claude", self.project)
        [m] = r["merged"]
        self.assertEqual(len(m["from"]), 3)
        self.assertIn(m["key"], self.keys())
        self.assertTrue(all(e["merged_into"] == m["key"] for e in ops.archived().values()))

    def test_undo_keeps_a_merged_talk_an_idle_terminal_has(self):
        r = compress.compress("claude", self.project)
        key = r["merged"][0]["key"]
        with mock.patch.object(BY_NAME["claude"], "live_pid", return_value=4242):
            u = compress.undo(self.project)
        self.assertEqual((len(u["restored"]), u["dropped"]), (3, []))
        self.assertIn(key, self.keys())

    def test_merge_writes_the_index_once_per_source(self):
        with mock.patch.object(ops, "_save", wraps=ops._save) as save:
            r = compress.compress("claude", self.project)
        self.assertEqual(len(r["merged"][0]["from"]), 3)
        self.assertEqual(save.call_count, 3 + 1)  # each archive, then Undo's start in one write
        index = ops.archived()
        self.assertEqual(len({e["merged_at"] for e in index.values()}), 1)

    def test_an_answer_without_the_opening_keeps_the_groups_title(self):
        self.answer = "## Goal\nTHE HAND-OVER\n"
        [m] = compress.compress("claude", self.project)["merged"]
        self.assertEqual((m["title"], m["name"], m["brief"], m["lessons"]),
                         ("Login fix", "Merged 3: Login fix", "same bug", []))

    def test_refusals(self):
        self.groups = []
        self.assertEqual(compress.compress("claude", self.project), {"merged": [], "errors": []})
        self.groups = "nonsense"
        with self.assertRaisesRegex(OpError, "didn't answer with a list"):
            compress.compress("claude", self.project)
        for agent, folder, why in (("nobody", self.project, "unknown agent"),
                                   ("gemini", self.project, "Gemini can't compress talks"),
                                   ("claude", self.path("gone"), "no longer exists"),
                                   ("claude", self.home, "fewer than two talks")):
            with self.assertRaisesRegex(OpError, why):
                compress.compress(agent, folder)
        self.assertEqual(len(self.keys()), 4)

    def test_cli(self):
        def run(args):
            out = io.StringIO()
            with redirect_stdout(out):
                code = assistant.cli(args)
            return code, out.getvalue().splitlines()

        self.assertEqual(run(["compress"]), (2, ["usage: --op compress FOLDER [AGENT] | --op uncompress FOLDER"]))
        code, lines = run(["compress", self.project])
        self.assertEqual(code, 0)
        self.assertRegex(lines[-4], r"^merged 3 talks into claude:[0-9a-f-]{36}: Merged 3: Login cookie fix$")
        self.assertEqual(lines[-3:], ["    The login broke twice; the cookie path was wrong and is fixed, with 3 tests.",
                                      "    lesson: Clear cookies before testing.", "    lesson: Next: add a logout test."])
        self.assertEqual(run(["uncompress", self.project]), (0, ["restored 3 talk(s), dropped 1 merged talk(s)"]))
        code, lines = run(["uncompress", self.project])
        self.assertEqual((code, lines[0].split(": ", 1)[1]), (1, "FAILED no compression to undo in ~/project"))


class Flags(HomeCase):
    def test_merge_runs_keep_the_session_and_take_no_tools(self):
        argv = claude.Claude().merge_argv("the-id", "Merged: x")
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertEqual(argv[argv.index("--session-id") + 1], "the-id")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--no-session-persistence", argv)
        self.assertNotIn("--ephemeral", BY_NAME["codex"].merge_argv("x", "y"))

    def test_rename(self):
        path = make_claude(self.home, self.project, "a", "first prompt")
        with open(path, "rb+") as f:      # a transcript cut off mid-line
            f.seek(-1, 2)
            f.truncate()
        make_codex(self.home, self.project, "c")
        os.makedirs(os.path.dirname(codex.CODEX_DB), exist_ok=True)
        con = sqlite3.connect(codex.CODEX_DB)
        with con:
            con.execute("CREATE TABLE threads (id, title, cwd, archived)")
            con.execute("INSERT INTO threads VALUES ('c', 'first prompt', ?, 0)", (self.project,))
        con.close()
        write(codex.CODEX_NAMES, "")
        self.assertEqual(ops.rename("claude:a", "  New\n name "), "New name")
        ops.rename("claude:a", "Newer name")
        ops.rename("codex:c", "Codex name")
        self.assertEqual({s["key"]: s["title"] for s in scan_all()},
                         {"claude:a": "Newer name", "codex:c": "Codex name"})
        self.assertEqual(read(path).count(b"\n"), read(path).count(b"{\"type\""))
        [named] = [json.loads(l) for l in read(codex.CODEX_NAMES).splitlines()]
        self.assertEqual((named["id"], named["thread_name"]), ("c", "Codex name"))
        make_gemini(self.home, self.project)
        key = next(s["key"] for s in scan_all() if s["tool"] == "gemini")
        for k, why in ((key, "renaming Gemini talks isn't supported"), ("claude:a", "needs a name")):
            with self.assertRaisesRegex(OpError, why):
                ops.rename(k, " " if k == "claude:a" else "x")

    def test_archive_refuses_live_unless_told_idle(self):
        path = make_codex(self.home, self.project, "c")
        os.utime(path, None)  # just written: Codex talks count as live for a while
        with self.assertRaisesRegex(OpError, "is running"):
            ops.archive("codex:c")
        ops.archive("codex:c", idle=True)
        ops.annotate("codex:c", merged_into="codex:m", merged_at=5.0)
        ops.annotate("codex:nope", merged_into="x")
        self.assertEqual(ops.archived()["codex:c"]["merged_into"], "codex:m")
        self.assertEqual(list(ops.archived()), ["codex:c"])
        self.assertEqual(read(ops.ARCHIVE_INDEX)[:1], b"{")
        self.assertEqual(MTIME, 1767268800)
        write(self.path("unused"), "")
