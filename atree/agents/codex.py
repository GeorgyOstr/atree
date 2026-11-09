"""Codex CLI: rollout JSONL files plus Codex's sqlite thread index."""
import json
import os
import re
import shutil
import sqlite3
import time
import urllib.parse

from . import base
from .base import Agent, OpError

CODEX_DIR = base._env("CODEX_HOME", os.path.join(base.HOME, ".codex"))
CODEX_SESSIONS = os.path.join(CODEX_DIR, "sessions")
CODEX_DB = os.path.join(CODEX_DIR, "state_5.sqlite")

LIVE_WINDOW = 180
META_KEYS = ("id", "cwd", "timestamp", "forked_from_id", "parent_thread_id", "git")
_TS = re.compile(rb'"timestamp":"((?:[^"\\]|\\.)*)"')
_LAST = re.compile(rb'"last_token_usage":\{[^{}]*?"total_tokens":(\d+)')
_TOTAL = re.compile(rb'"total_token_usage":\{[^{}]*?"total_tokens":(\d+)')


def _init():
    return {"meta": None, "bad": False, "prompts": 0, "days": {}, "context": 0, "tokens": 0}


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
            st["tokens"] = int(m.group(1))


def _finish(st):
    if st["bad"] or st["meta"] is None:
        return None
    return {"meta": st["meta"], "prompts": st["prompts"], "days": dict(st["days"]),
            "context": st["context"], "tokens": st["tokens"]}


def rollouts():
    out = []
    for root, _dirs, files in os.walk(CODEX_SESSIONS):
        for f in files:
            if f.startswith("rollout-") and f.endswith(".jsonl"):
                out.append(os.path.join(root, f))
    return out


def _index():
    """(rows by id, spawn parent by child id) from Codex's thread index."""
    rows, edges = {}, {}
    if not os.path.exists(CODEX_DB):
        return rows, edges
    try:
        con = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(CODEX_DB), uri=True, timeout=2)
    except sqlite3.Error:
        return rows, edges
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
        pass
    finally:
        con.close()
    return rows, edges


def _update_index(sql, args):
    if not os.path.exists(CODEX_DB):
        return
    con = sqlite3.connect(CODEX_DB, timeout=5)
    try:
        with con:
            con.execute(sql, args)
    finally:
        con.close()


def _tail_rate_limits(path, limit=4_000_000):
    with open(path, "rb") as f:
        size = f.seek(0, 2)
        f.seek(max(0, size - limit))
        data = f.read()
    for line in reversed(data.split(b"\n")):
        if b'"rate_limits":{' not in line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        payload = rec.get("payload") if isinstance(rec, dict) else None
        rl = payload.get("rate_limits") if isinstance(payload, dict) else None
        if isinstance(rl, dict) and rl:
            return rl, base.iso_to_epoch(rec.get("timestamp"))
    return None, None


class Codex(Agent):
    name = "codex"
    label = "Codex"
    color = "#5b8cff"
    can_move = True
    new_command = "codex"
    continue_command = "codex resume --last"

    def resume_command(self, s):
        return "codex resume %s" % s["id"]

    def ask_argv(self):
        return ["codex", "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "-"]

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
            live = now - st.st_mtime < LIVE_WINDOW
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
                context=info["context"], tokens=info["tokens"]))
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
        for mtime, p in sorted(paths, reverse=True)[:5]:
            try:
                found, observed = _tail_rate_limits(p)
            except OSError:
                continue
            if found:
                observed = observed or mtime
                break
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
