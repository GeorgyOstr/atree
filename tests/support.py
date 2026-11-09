"""Test helpers: a throwaway HOME with every agent's data redirected into it."""
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from atree import aisearch, assistant, ops  # noqa: E402
from atree.agents import base, claude, codex, copilot, gemini, opencode  # noqa: E402

TS = "2026-01-01T12:00:00Z"
MTIME = 1767268800


def paths_for(home):
    data = os.path.join(home, "data", "atree")
    return [
        (base, "HOME", home), (base, "DATA", data), (base, "BACKUPS", os.path.join(data, "backups")),
        (ops, "DATA", data), (ops, "ARCHIVE_DIR", os.path.join(data, "archive")),
        (ops, "ARCHIVE_INDEX", os.path.join(data, "archive", "index.json")),
        (ops, "TASKS_FILE", os.path.join(data, "tasks.json")),
        (assistant, "AGENT_DIR", os.path.join(data, "agent")),
        (aisearch, "SEARCH_DIR", os.path.join(data, "search")),
        (claude, "CLAUDE_DIR", os.path.join(home, ".claude")),
        (claude, "CLAUDE_PROJECTS", os.path.join(home, ".claude", "projects")),
        (claude, "CLAUDE_LIVE", os.path.join(home, ".claude", "sessions")),
        (codex, "CODEX_DIR", os.path.join(home, ".codex")),
        (codex, "CODEX_SESSIONS", os.path.join(home, ".codex", "sessions")),
        (codex, "CODEX_DB", os.path.join(home, ".codex", "state_5.sqlite")),
        (gemini, "GEMINI_DIR", os.path.join(home, ".gemini")),
        (gemini, "GEMINI_TMP", os.path.join(home, ".gemini", "tmp")),
        (opencode, "DATA", os.path.join(home, "data", "opencode")),
        (opencode, "DB", os.path.join(home, "data", "opencode", "opencode.db")),
        (opencode, "SERVICE", os.path.join(home, "state", "opencode", "service.json")),
        (copilot, "COPILOT_DIR", os.path.join(home, ".copilot")),
        (copilot, "STATE", os.path.join(home, ".copilot", "session-state")),
    ]


class HomeCase(unittest.TestCase):
    """Each test gets a fresh HOME; caches are cleared."""

    def setUp(self):
        self.home = os.path.realpath(tempfile.mkdtemp(prefix="atree-test-"))
        self.addCleanup(shutil.rmtree, self.home, True)
        for mod, name, value in paths_for(self.home):
            p = mock.patch.object(mod, name, value)
            p.start()
            self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {"HOME": self.home, "XDG_DATA_HOME": os.path.join(self.home, "data"),
                                           "XDG_CACHE_HOME": os.path.join(self.home, "cache")})
        env.start()
        self.addCleanup(env.stop)
        for c in (base._cache, base._lines_cache):
            c.clear()
        opencode._state.update(key=None, rows=[])
        self.project = os.path.join(self.home, "project")
        os.makedirs(self.project)

    def path(self, *parts):
        return os.path.join(self.home, *parts)


def write(path, text, mtime=MTIME):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w" if isinstance(text, str) else "wb") as f:
        f.write(text)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def jsonl(*records):
    return "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in records)


def make_claude(home, cwd, sid="claude-fixture", prompt="hello claude", extra=()):
    path = os.path.join(home, ".claude", "projects", claude.encode(cwd), sid + ".jsonl")
    return write(path, jsonl({"type": "user", "cwd": cwd, "timestamp": TS,
                              "uuid": "00000000-0000-0000-0000-%012d" % abs(hash(sid) % 10**12),
                              "message": {"role": "user", "content": prompt}}, *extra))


def make_codex(home, cwd, sid="codex-fixture", extra=(), meta=None):
    payload = {"id": sid, "cwd": cwd, "timestamp": TS}
    payload.update(meta or {})
    path = os.path.join(home, ".codex", "sessions", "2026", "01", "01", "rollout-%s.jsonl" % sid)
    return write(path, jsonl({"type": "session_meta", "payload": payload},
                             {"timestamp": TS, "type": "event_msg", "payload": {"type": "task_started"}}, *extra))


def make_gemini(home, cwd, sid="gemini-fixture"):
    d = os.path.join(home, ".gemini", "tmp", "project")
    write(os.path.join(d, ".project_root"), cwd)
    return write(os.path.join(d, "chats", "session-%s.jsonl" % sid), jsonl(
        {"sessionId": sid, "projectHash": "project", "startTime": TS},
        {"id": "prompt", "type": "user", "timestamp": TS, "content": "hello gemini"}))


def make_copilot(home, cwd, sid="copilot-fixture"):
    d = os.path.join(home, ".copilot", "session-state", sid)
    write(os.path.join(d, "workspace.yaml"), "id: %s\ncwd: %s\n" % (sid, cwd))
    return write(os.path.join(d, "events.jsonl"), jsonl(
        {"type": "session.start", "timestamp": TS, "data": {"context": {"cwd": cwd}}},
        {"type": "user.message", "timestamp": TS, "data": {"content": "hello copilot"}}))


def make_opencode(home, cwd, sid="opencode-fixture"):
    db = os.path.join(home, "data", "opencode", "opencode.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db)
    with con:
        con.execute("CREATE TABLE session_v2 (id, directory, title, time_created, time_updated)")
        con.execute("CREATE TABLE session_message (id, session_id, type, seq, time_created, data)")
        con.execute("INSERT INTO session_v2 VALUES (?,?,?,?,?)", (sid, cwd, "OpenCode talk", MTIME * 1000, MTIME * 1000))
        con.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?)",
                    ("m1", sid, "user", 1, MTIME * 1000, json.dumps({"text": "hello opencode"})))
    con.close()
    return db


def make_all(home, cwd):
    make_claude(home, cwd)
    make_codex(home, cwd)
    make_gemini(home, cwd)
    make_copilot(home, cwd)
    make_opencode(home, cwd)


def tree_state(root):
    """{relative path: (kind, bytes or link target, mode, mtime_ns)} for comparing trees."""
    out = {}
    for d, dirs, files in os.walk(root):
        for name in dirs + files:
            p = os.path.join(d, name)
            st = os.lstat(p)
            rel = os.path.relpath(p, root)
            if os.path.islink(p):
                out[rel] = ("link", os.readlink(p))
            elif os.path.isdir(p):
                out[rel] = ("dir", st.st_mode)
            else:
                with open(p, "rb") as f:
                    out[rel] = ("file", f.read(), st.st_mode, st.st_mtime_ns)
    return out


def read(path, mode="rb"):
    with open(path, mode) as f:
        return f.read()
