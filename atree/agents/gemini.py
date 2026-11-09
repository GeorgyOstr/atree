"""Gemini CLI: ~/.gemini/tmp/<project>/chats/session-*.jsonl (or older .json)."""
import hashlib
import json
import os
import re
import time

from . import base
from .base import Agent

GEMINI_DIR = os.path.join(base._env("GEMINI_CLI_HOME", base.HOME), ".gemini")
GEMINI_TMP = os.path.join(GEMINI_DIR, "tmp")

LIVE_WINDOW = 180
META_KEYS = ("sessionId", "kind", "summary", "startTime", "lastUpdated")
NOT_PROMPTS = ("/", "?", "<session_context>", "<hook_context>")


def prompt_text(content):
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        text = "".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
    elif isinstance(content, dict) and isinstance(content.get("text"), str):
        text = content["text"]
    else:
        return None
    text = text.strip()
    if not text or text.startswith(NOT_PROMPTS):
        return None
    return text[:400]


def _num(v):
    return v if type(v) in (int, float) else 0


def _summary(msg):
    out = {"type": msg.get("type")}
    if isinstance(msg.get("timestamp"), str):
        out["timestamp"] = msg["timestamp"]
    if msg.get("type") == "user":
        out["prompt"] = prompt_text(msg.get("content"))
    elif msg.get("type") == "gemini" and isinstance(msg.get("tokens"), dict):
        t = msg["tokens"]
        out["tokens"] = (_num(t.get("input")) + _num(t.get("output")), _num(t.get("total")))
    return out


def _init():
    return {"meta": {}, "msgs": {}}


def _add(st, messages):
    for m in messages:
        if isinstance(m, dict) and isinstance(m.get("id"), str) and "$patch" not in m:
            st["msgs"][m["id"]] = _summary(m)


def _set_meta(st, rec):
    for k in META_KEYS:
        if k in rec:
            st["meta"][k] = rec[k]


def _patch(st, patch):
    msgs = st["msgs"]
    for p in [patch] + [u for u in patch.get("updates") or [] if isinstance(u, dict)]:
        if not isinstance(p, dict):
            continue
        mid = p.get("id")
        if isinstance(mid, str) and msgs.get(mid, {}).get("type") == "user" and p.get("content") is not None:
            msgs[mid]["prompt"] = prompt_text(p["content"])
    for rid in patch.get("removeIds") or []:
        if isinstance(rid, str):
            msgs.pop(rid, None)
    order = patch.get("orderIds")
    if isinstance(order, list):
        seen = set()
        for oid in order:
            if isinstance(oid, str) and oid in msgs and oid not in seen:
                seen.add(oid)
                msgs[oid] = msgs.pop(oid)


def _step(st, line):
    try:
        rec = json.loads(line)
    except ValueError:
        return
    if not isinstance(rec, dict):
        return
    msgs = st["msgs"]
    if isinstance(rec.get("$rewindTo"), str):
        target = rec["$rewindTo"]
        keys = list(msgs)
        cut = keys.index(target) if target in msgs else 0
        for k in keys[cut:]:
            del msgs[k]
    elif isinstance(rec.get("$patch"), dict):
        _patch(st, rec["$patch"])
    elif isinstance(rec.get("id"), str):
        msgs[rec["id"]] = _summary(rec)  # replacing keeps the original position
    elif isinstance(rec.get("$set"), dict):
        s = rec["$set"]
        if isinstance(s.get("messages"), list):
            msgs.clear()
            _add(st, s["messages"])
        _set_meta(st, s)
    elif isinstance(rec.get("sessionId"), str) and isinstance(rec.get("projectHash"), str):
        _set_meta(st, rec)
        if isinstance(rec.get("messages"), list):
            _add(st, rec["messages"])


def _finish(st):
    info = {"meta": dict(st["meta"]), "first": None, "last": None, "prompts": 0,
            "days": {}, "first_msg": "", "context": 0, "tokens": 0}
    for m in st["msgs"].values():
        ts = m.get("timestamp")
        if ts:
            info["first"] = info["first"] or ts
            info["last"] = ts
        if m.get("prompt"):
            info["prompts"] += 1
            base.count_day(info["days"], ts)
            info["first_msg"] = info["first_msg"] or m["prompt"]
        elif m.get("tokens"):
            info["context"] = m["tokens"][0]
            info["tokens"] += m["tokens"][1]
    return info


def _parse_json(path):
    try:
        with open(path, "rb") as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict):
        return None
    st = _init()
    _set_meta(st, rec)
    if isinstance(rec.get("messages"), list):
        _add(st, rec["messages"])
    return _finish(st)


def _known_projects():
    try:
        with open(os.path.join(GEMINI_DIR, "projects.json"), "rb") as f:
            projects = json.load(f).get("projects")
    except (OSError, ValueError, AttributeError):
        return {}
    out = {}
    if isinstance(projects, dict):
        for path, slug in projects.items():
            if not isinstance(path, str):
                continue
            out[hashlib.sha256(path.encode()).hexdigest()] = path
            if isinstance(slug, str):
                out[slug] = path
    return out


def chats():
    """[(cwd, chat path)] for every top-level chat file."""
    if not os.path.isdir(GEMINI_TMP):
        return []
    known = None
    out = []
    for d in os.scandir(GEMINI_TMP):
        chat_dir = os.path.join(d.path, "chats")
        if not os.path.isdir(chat_dir):
            continue
        try:
            with open(os.path.join(d.path, ".project_root"), encoding="utf-8") as f:
                cwd = f.read().strip()
        except (OSError, UnicodeDecodeError):
            if known is None:
                known = _known_projects()
            cwd = known.get(d.name)
        if not cwd:
            continue
        for f in os.scandir(chat_dir):
            if f.name.startswith("session-") and f.name.endswith((".jsonl", ".json")) and f.is_file():
                out.append((cwd, f.path))
    return out


def read_chat(path):
    if path.endswith(".jsonl"):
        return base.cached_lines(path, _init, _step, _finish)
    return base.cached(path, _parse_json)


class Gemini(Agent):
    name = "gemini"
    label = "Gemini"
    color = "#2f9e7a"
    new_command = "gemini"
    continue_command = "gemini --resume latest"

    def resume_command(self, s):
        return "gemini --resume %s" % s["id"]

    def ask_argv(self):
        return ["gemini", "-p", "Answer the request given on standard input."]

    def scan(self):
        now = time.time()
        best = {}
        for cwd, path in chats():
            try:
                info, st = read_chat(path)
            except OSError:
                continue
            if not info:
                continue
            meta = info["meta"]
            sid = meta.get("sessionId")
            if not isinstance(sid, str) or meta.get("kind") == "subagent" or info["prompts"] <= 0:
                continue
            start = meta.get("startTime") if isinstance(meta.get("startTime"), str) else info["first"]
            last = meta.get("lastUpdated") if isinstance(meta.get("lastUpdated"), str) else info["last"]
            summary = meta.get("summary")
            live = now - st.st_mtime < LIVE_WINDOW
            t = base.talk(
                "gemini", sid, cwd=cwd,
                title=base.pick_title(summary if isinstance(summary, str) else None,
                                      info["first_msg"][:80], sid=sid),
                first_msg=info["first_msg"],
                created=base.iso_to_epoch(start) or st.st_mtime,
                updated=max(base.iso_to_epoch(last) or 0, st.st_mtime),
                prompts=info["prompts"], days=info["days"], size=st.st_size, path=path,
                live=live, live_status="active" if live else None,
                context=info["context"], tokens=info["tokens"])
            rank = (t["updated"], path.endswith(".jsonl"))
            if sid not in best or rank > best[sid][0]:
                best[sid] = (rank, t)
        return [t for _r, t in best.values()]

    def archive_members(self, s):
        members = []
        for _cwd, path in chats():
            try:
                info, _st = read_chat(path)
            except OSError:
                continue
            if not info or info["meta"].get("sessionId") != s["id"]:
                continue
            members.append(path)
            sub = os.path.join(os.path.dirname(path), re.sub(r"[^A-Za-z0-9_-]", "_", s["id"]))
            if os.path.isdir(sub) and sub not in members:
                members.append(sub)
        return GEMINI_TMP, members or [s["path"]]
