"""Codex CLI: rollout JSONL files plus Codex's sqlite thread index."""
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import urllib.parse

from . import base
from .base import Agent, OpError

CODEX_DIR = base._env("CODEX_HOME", os.path.join(base.HOME, ".codex"))
CODEX_SESSIONS = os.path.join(CODEX_DIR, "sessions")
CODEX_DB = os.path.join(CODEX_DIR, "state_5.sqlite")
CODEX_NAMES = os.path.join(CODEX_DIR, "session_index.jsonl")

LIVE_WINDOW = 180
META_KEYS = ("id", "cwd", "timestamp", "forked_from_id", "parent_thread_id", "git")
_TS = re.compile(rb'"timestamp":"((?:[^"\\]|\\.)*)"')
_LAST = re.compile(rb'"last_token_usage":\{[^{}]*?"total_tokens":(\d+)')
_SESSION = re.compile(r"^session id: ([\w-]+)\s*$", re.M)
_TOTAL = re.compile(rb'"total_token_usage":\{[^{}]*?"total_tokens":(\d+)')


def _init():
    return {"meta": None, "bad": False, "prompts": 0, "days": {}, "context": 0, "tokens": 0, "tdays": {}}


def _step(st, line):
    if st["bad"]:
        return
    if st["meta"] is None:
        try:
            rec = json.loads(line)
        except ValueError:
            rec = None
        payload = rec.get("payload") if isinstance(rec, dict) and rec.get("type") == "session_meta" else None
        if not isinstance(payload, dict):
            st["bad"] = True
            return
        st["meta"] = {k: payload.get(k) for k in META_KEYS}
        return
    if b'"type":"task_started"' in line:
        m = _TS.search(line)
        if m:
            st["prompts"] += 1
            base.count_day(st["days"], json.loads(b'"' + m.group(1) + b'"'))
    elif b'"type":"token_count"' in line:
        m = _LAST.search(line)
        if m:
            st["context"] = int(m.group(1))
        m = _TOTAL.search(line)
        if m:
            total = int(m.group(1))
            if total > st["tokens"]:  # the running total: its growth is this line's spend
                ts = _TS.search(line)
                base.add_day(st["tdays"], json.loads(b'"' + ts.group(1) + b'"') if ts else None,
                             total - st["tokens"])
            st["tokens"] = total


def _finish(st):
    if st["bad"] or st["meta"] is None:
        return None
    return {"meta": st["meta"], "prompts": st["prompts"], "days": dict(st["days"]),
            "context": st["context"], "tokens": st["tokens"], "tdays": dict(st["tdays"])}


def rollouts():
    out = []
    for root, _dirs, files in os.walk(CODEX_SESSIONS):
        for f in files:
            if f.startswith("rollout-") and f.endswith(".jsonl"):
                out.append(os.path.join(root, f))
    return out


_index_cache = {"key": None, "value": None}
_index_lock = threading.Lock()  # scans run on several threads; key and value change together


def _file_key(path):
    try:
        return base.stat_key(os.stat(path))
    except OSError:
        return None


def _index():
    """(rows by id, spawn parent by child id), re-read only when the database or its WAL changed."""
    with _index_lock:
        key = (CODEX_DB, _file_key(CODEX_DB), _file_key(CODEX_DB + "-wal"))
        if key[1] is not None and key == _index_cache["key"]:
            return _index_cache["value"]
        rows, edges, ok = _read_index()
        if not ok:  # e.g. locked: keep the last good read and retry on the next scan
            return _index_cache["value"] or (rows, edges)
        _index_cache["key"], _index_cache["value"] = key, (rows, edges)
        return _index_cache["value"]


def _read_index():
    """(rows, edges, ok); ok is False when a database error cut the read short."""
    rows, edges = {}, {}
    if not os.path.exists(CODEX_DB):
        return rows, edges, True
    try:
        con = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(CODEX_DB), uri=True, timeout=2)
    except sqlite3.Error:
        return rows, edges, False
    try:
        con.row_factory = sqlite3.Row
        cols = {r[1] for r in con.execute("PRAGMA table_info(threads)")}
        want = [c for c in ("id", "created_at", "updated_at", "cwd", "title", "archived",
                            "git_branch", "first_user_message", "agent_nickname", "agent_role")
                if c in cols]
        if "id" in want:
            for r in con.execute("SELECT %s FROM threads" % ", ".join(want)):
                rows[r["id"]] = dict(r)
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_spawn_edges'").fetchone():
            for parent, child in con.execute("SELECT parent_thread_id, child_thread_id FROM thread_spawn_edges"):
                edges[child] = parent
    except sqlite3.Error:
        return rows, edges, False
    finally:
        con.close()
    return rows, edges, True


def _update_index(sql, args):
    """The number of rows changed; None when there is no index."""
    if not os.path.exists(CODEX_DB):
        return None
    con = sqlite3.connect(CODEX_DB, timeout=5)
    try:
        with con:
            return con.execute(sql, args).rowcount
    finally:
        con.close()


def _rate_limits(line):
    if b'"rate_limits":{' not in line:
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    payload = rec.get("payload") if isinstance(rec, dict) else None
    rl = payload.get("rate_limits") if isinstance(payload, dict) else None
    if isinstance(rl, dict) and rl:
        return rl, base.iso_to_epoch(rec.get("timestamp"))
    return None


def _tail_rate_limits(path, limit=4_000_000, chunk=1 << 16):
    """The last rate-limit record within ``limit`` bytes of the end, read backwards in chunks."""
    with open(path, "rb") as f:
        pos = f.seek(0, 2)
        stop = max(0, pos - limit)
        rest = b""  # the start of a line whose beginning is not read yet
        while pos > stop:
            step = min(chunk, pos - stop)
            pos -= step
            f.seek(pos)
            lines = (f.read(step) + rest).split(b"\n")
            rest = lines.pop(0) if pos > stop else b""
            for line in reversed(lines):
                found = _rate_limits(line)
                if found:
                    return found
    return None, None


class Codex(Agent):
    name = "codex"
    label = "Codex"
    color = "#5b8cff"
    can_move = True
    can_merge = True
    new_command = "codex"
    continue_command = "codex resume --last"

    def resume_command(self, s):
        return "codex resume %s" % s["id"]

    def ask_argv(self):
        return ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-"]

    def merge_argv(self, sid, name):
        # Codex can't run without its shell tool, so text inside the transcripts it merges could
        # make it read local files into the merged talk: Claude (no tools at all) merges instead
        return []

    def merged_id(self, sid, err):
        m = _SESSION.search(err or "")
        return m.group(1) if m else None

    def rename(self, s, title):
        try:
            changed = _update_index("UPDATE threads SET title = ? WHERE id = ?", (title, s["id"]))
        except sqlite3.Error as e:
            raise OpError("Codex's thread index couldn't be updated (%s)" % e) from e
        if not changed:  # titles are read from the index only
            raise OpError("Codex's thread index has no entry for '%s'" % s["title"])
        if os.path.exists(CODEX_NAMES):  # the names Codex's own resume list shows; the last one counts
            stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000000000Z"
            line = json.dumps({"id": s["id"], "thread_name": title, "updated_at": stamp}, separators=(",", ":"))
            try:
                with open(CODEX_NAMES, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def transcript(self, s):
        out = []
        with open(s["path"], "rb") as f:
            for line in f:
                if b'"response_item"' not in line or b'"message"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                p = rec.get("payload") if isinstance(rec, dict) and rec.get("type") == "response_item" else None
                if not isinstance(p, dict) or p.get("type") != "message" or p.get("role") not in ("user", "assistant"):
                    continue
                text = base.all_text(p.get("content")).strip()
                if p["role"] == "assistant":
                    if text:
                        out.append(("agent", text))
                elif text and not text.startswith("<"):  # <environment_context> and the like
                    out.append(("user", text))
        return out

    def scan(self):
        if not os.path.isdir(CODEX_SESSIONS):
            return []
        rows, edges = _index()
        now = time.time()
        talks = []
        for path in rollouts():
            try:
                info, st = base.cached_lines(path, _init, _step, _finish)
            except OSError:
                continue
            if not info or not isinstance(info["meta"].get("id"), str) or not info["meta"]["id"]:
                continue
            meta = info["meta"]
            sid = meta["id"]
            row = rows.get(sid, {})
            if row.get("archived"):
                continue
            parent = edges.get(sid) or meta.get("parent_thread_id")
            fork = meta.get("forked_from_id")
            related = [{"key": "codex:%s" % fork, "kind": "fork"}] if fork and fork != parent else []
            first_msg = (row.get("first_user_message") or "")[:400]
            title = base.pick_title(row.get("title"), row.get("agent_nickname"), first_msg[:80], sid=sid)
            if parent and row.get("agent_role"):
                title = "[%s] %s" % (row["agent_role"], title)
            git = meta.get("git")
            live = abs(now - st.st_mtime) < LIVE_WINDOW  # abs: an mtime far ahead is a clock step
            talks.append(base.talk(
                "codex", sid,
                cwd=row.get("cwd") or meta.get("cwd") or "/",
                title=title, first_msg=first_msg,
                created=row.get("created_at") or base.iso_to_epoch(meta.get("timestamp")) or st.st_mtime,
                updated=max(row.get("updated_at") or 0, st.st_mtime),
                prompts=info["prompts"], days=info["days"],
                branch=row.get("git_branch") or (git.get("branch") if isinstance(git, dict) else None) or None,
                size=st.st_size, path=path, related=related,
                parent="codex:%s" % parent if parent else None,
                live=live, live_status="active" if live else None,
                context=info["context"], tokens=info["tokens"], tdays=info["tdays"]))
        return talks

    def move(self, s, target):
        rollout = s["path"]
        saved = os.path.join(base.backup([rollout], "move-" + s["id"][:8]), os.path.basename(rollout))
        base.rewrite_jsonl(rollout, rollout, s["cwd"], target)
        try:
            _update_index("UPDATE threads SET cwd = ? WHERE id = ?", (target, s["id"]))
        except sqlite3.Error as e:
            shutil.copy2(saved, rollout)
            raise OpError("Codex's thread index couldn't be updated (%s); the talk stays in %s"
                          % (e, s["cwd"])) from e

    def archive_members(self, s):
        return CODEX_SESSIONS, [s["path"]]

    def after_archive(self, s):
        _update_index("UPDATE threads SET archived = 1, archived_at = ? WHERE id = ?",
                      (int(time.time()), s["id"]))

    def after_restore(self, entry):
        _update_index("UPDATE threads SET archived = 0, archived_at = NULL WHERE id = ?", (entry["id"],))

    def usage(self):
        found = observed = None
        paths = []
        for p in rollouts():
            try:
                paths.append((os.path.getmtime(p), p))
            except OSError:
                pass
        # a resumed old talk can be the newest file yet hold only old records: take the newest
        # record of the five newest files (a file holds nothing newer than its mtime)
        for mtime, p in sorted(paths, reverse=True)[:5]:
            if found and observed >= mtime:
                break
            try:
                rl, seen = _tail_rate_limits(p)
            except OSError:
                continue
            seen = seen or mtime
            if rl and (not found or seen > observed):
                found, observed = rl, seen
        if not found:
            return {"error": "no Codex rate-limit data yet"}
        now = time.time()
        bars = []
        for key in ("primary", "secondary"):
            b = found.get(key)
            if not isinstance(b, dict) or not b:
                continue
            minutes = b.get("window_minutes") or 0
            label = "week" if minutes >= 10080 else "session (%dh)" % (minutes // 60) if minutes else key
            resets = b.get("resets_at")
            stale = bool(resets) and resets < now
            bars.append({"label": label, "used": 0.0 if stale else float(b.get("used_percent") or 0),
                         "resets_at": resets, "window": minutes * 60, "stale": stale})
        bars.sort(key=lambda b: b["label"] == "week")
        return {"bars": bars, "plan": found.get("plan_type"), "observed_at": observed}
