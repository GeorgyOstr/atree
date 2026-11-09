"""OpenCode: sessions are rows in OpenCode's sqlite database."""
import json
import os
import sqlite3
import threading
import time
import urllib.parse

from . import base
from .base import Agent

DATA = os.path.join(base._env("XDG_DATA_HOME", os.path.join(base.HOME, ".local", "share")), "opencode")
DB = os.path.join(DATA, base._env("OPENCODE_DB", "opencode.db"))
SERVICE = os.path.join(base._env("XDG_STATE_HOME", os.path.join(base.HOME, ".local", "state")),
                       "opencode", "service.json")

LIVE_WINDOW = 180
SESSION_COLS = ("id", "parent_id", "directory", "title", "time_created", "time_updated",
                "time_archived", "time_suspended", "time_busy", "time_idle", "tokens_input",
                "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write")
TOKEN_COLS = SESSION_COLS[-5:]

_lock = threading.Lock()
_state = {"key": None, "rows": []}


def _num(v):
    return v if type(v) in (int, float) else 0


def _data(raw):
    try:
        d = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _first_text(d):
    for k in ("text", "prompt", "content"):
        if isinstance(d.get(k), str):
            return d[k]
    parts = d.get("parts")
    if not isinstance(parts, list):
        parts = d.get("content") if isinstance(d.get("content"), list) else []
    for p in parts:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            return p["text"]
    return ""


def _tables(con):
    return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(con, table):
    return [r[1] for r in con.execute("PRAGMA table_info(%s)" % table)]


def _read(path):
    con = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(path), uri=True, timeout=2)
    try:
        tables = _tables(con)
        v2 = "session_v2" in tables
        stable, mtable = ("session_v2", "session_message") if v2 else ("session", "message")
        if stable not in tables:
            return []
        have = set(_cols(con, stable))
        cols = [c for c in SESSION_COLS if c in have]
        sessions = {}
        for row in con.execute("SELECT %s FROM %s" % (", ".join(cols), stable)):
            s = dict(zip(cols, row))
            s.update(prompts=0, days={}, first_msg="", context=0, msg_tokens=0)
            sessions[s.get("id")] = s
        if mtable not in tables:
            return list(sessions.values())
        mcols = set(_cols(con, mtable))
        order = "session_id, seq" if "seq" in mcols else "session_id, time_created, id"
        select = "id, session_id, time_created, data" + (", type" if v2 else "")
        pending = {}  # 1.x: session id -> message id whose parts hold the first prompt
        for row in con.execute("SELECT %s FROM %s ORDER BY %s" % (select, mtable, order)):
            mid, sid, created, raw = row[:4]
            s = sessions.get(sid)
            if s is None:
                continue
            d = _data(raw)
            role = row[4] if v2 else d.get("role")
            if role == "user":
                s["prompts"] += 1
                if type(created) in (int, float) and created > 0:
                    day = base.local_day(created / 1000)
                    s["days"][day] = s["days"].get(day, 0) + 1
                if not s["first_msg"]:
                    s["first_msg"] = _first_text(d)[:400]
                    if not s["first_msg"] and not v2 and sid not in pending:
                        pending[sid] = mid
            elif role == "assistant" and isinstance(d.get("tokens"), dict):
                t = d["tokens"]
                cache = t.get("cache") if isinstance(t.get("cache"), dict) else {}
                ctx = (_num(t.get("input")) + _num(t.get("output")) + _num(t.get("reasoning"))
                       + _num(cache.get("read")) + _num(cache.get("write")))
                s["context"] = ctx
                s["msg_tokens"] += ctx
        if pending and "part" in tables:
            for sid, mid in pending.items():
                for (raw,) in con.execute("SELECT data FROM part WHERE message_id = ? ORDER BY id", (mid,)):
                    p = _data(raw)
                    if p.get("type") == "text" and isinstance(p.get("text"), str) and not p.get("synthetic"):
                        sessions[sid]["first_msg"] = p["text"].strip()[:400]
                        break
        return list(sessions.values())
    finally:
        con.close()


def _file_key(path):
    try:
        return base.stat_key(os.stat(path))
    except FileNotFoundError:
        return None


def rows():
    """Session rows, re-read only when the database or its WAL changed."""
    path = DB
    with _lock:
        try:
            key = (path, _file_key(path), _file_key(path + "-wal"))
        except OSError:
            return _state["rows"]
        if key == _state["key"]:
            return _state["rows"]
        try:
            _state["rows"] = _read(path)
            _state["key"] = key
        except sqlite3.Error as e:
            print("atree: %s: %s" % (path, e))
        return _state["rows"]


def _server_up():
    try:
        with open(SERVICE, "rb") as f:
            pid = int(json.load(f)["pid"])
    except (OSError, ValueError, TypeError, KeyError, OverflowError):
        return False
    return pid > 0 and base.pid_alive(pid)


def _int(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


class OpenCode(Agent):
    name = "opencode"
    label = "OpenCode"
    color = "#b06ccf"
    can_archive = False
    new_command = "opencode"
    continue_command = "opencode --continue"

    def resume_command(self, s):
        return "opencode --session %s" % s["id"]

    def live_pid(self, s):
        # every session shares one database file, so only a resume's argument identifies one
        return base.session_pid(dict(s, path=None))

    def scan(self):
        if not os.path.exists(DB):
            return []
        now = time.time()
        server = None
        talks = []
        for r in rows():
            if r.get("time_archived") or not r["prompts"] or not isinstance(r.get("id"), str):
                continue
            sid = r["id"]
            updated = (r.get("time_updated") or r.get("time_created") or 0) / 1000
            busy = bool(r.get("time_suspended")) or _int(r.get("time_busy")) > _int(r.get("time_idle"))
            if busy:
                if server is None:
                    server = _server_up()
                busy = server
            live = busy or now - updated < LIVE_WINDOW
            tokens = sum(_num(r.get(c)) for c in TOKEN_COLS) or r["msg_tokens"]
            talks.append(base.talk(
                "opencode", sid, cwd=r.get("directory") or "/",
                title=base.pick_title(r.get("title"), r["first_msg"][:80], sid=sid, n=12),
                first_msg=r["first_msg"],
                created=(r.get("time_created") or 0) / 1000 or updated, updated=updated,
                prompts=r["prompts"], days=dict(r["days"]), path=DB,
                parent="opencode:%s" % r["parent_id"] if r.get("parent_id") else None,
                live=live, live_status="busy" if busy else "active" if live else None,
                context=r["context"], tokens=tokens))
        return talks
