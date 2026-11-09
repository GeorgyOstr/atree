"""Claude Code: ~/.claude/projects/<encoded cwd>/<id>.jsonl."""
import errno
import glob
import json
import os
import re
import shlex
import shutil
import time

from . import base
from .base import Agent, OpError

CLAUDE_DIR = base._env("CLAUDE_CONFIG_DIR", os.path.join(base.HOME, ".claude"))
CLAUDE_PROJECTS = os.path.join(CLAUDE_DIR, "projects")
CLAUDE_LIVE = os.path.join(CLAUDE_DIR, "sessions")

_STR = rb'"((?:[^"\\]|\\.)*)"'
_CWD = re.compile(rb'"cwd":' + _STR)
_TS = re.compile(rb'"timestamp":' + _STR)
_BRANCH = re.compile(rb'"gitBranch":' + _STR)
_UUID = re.compile(rb'"uuid":"([0-9a-f-]{36})"')
_PARENT = re.compile(rb'"parentUuid":"([0-9a-f-]{36})"')
_MSG_ID = re.compile(rb'"id":"(msg_(?:[^"\\]|\\.)*)"')
_NUMS = {k: re.compile(rb'"' + k + rb'":(\d+)') for k in (
    b"input_tokens", b"cache_creation_input_tokens", b"cache_read_input_tokens", b"output_tokens")}


def _unescape(raw):
    if b"\\" not in raw:
        return raw.decode("utf-8", "replace")
    try:
        return json.loads(b'"' + raw + b'"')
    except ValueError:
        return raw.decode("utf-8", "replace")


def _loads(line):
    """The record on a line, or None if it isn't JSON."""
    try:
        return json.loads(line)
    except ValueError:
        return None


def _init():
    return {"cwd": None, "first": None, "last": None, "branch": None, "ai": None,
            "custom": None, "first_msg": None, "prompts": 0, "uuids": set(),
            "ext_parent": None, "days": {}, "context": 0, "total": 0,
            "msg": None, "msg_tokens": 0}


def _step(st, line):
    if st["cwd"] is None:
        m = _CWD.search(line)
        if m:
            st["cwd"] = _unescape(m.group(1))
    m = _TS.search(line)
    if m:
        ts = _unescape(m.group(1))
        if st["first"] is None:
            st["first"] = ts
        st["last"] = ts
    if st["branch"] is None:
        m = _BRANCH.search(line)
        if m:
            st["branch"] = _unescape(m.group(1))
    m = _PARENT.search(line)
    if m and st["ext_parent"] is None:
        p = m.group(1).decode()
        if p not in st["uuids"]:
            st["ext_parent"] = p
    m = _UUID.search(line)
    if m:
        st["uuids"].add(m.group(1).decode())
    if b'"type":"assistant"' in line and b'"usage":{' in line and b'"isSidechain":true' not in line:
        usage = line[line.index(b'"usage":{'):]
        ctx = 0
        for rx in _NUMS.values():
            n = rx.search(usage)
            if n:
                ctx += int(n.group(1))
        m = _MSG_ID.search(line)
        mid = m.group(1).decode() if m else None
        if mid != st["msg"]:
            st["total"] += st["msg_tokens"]
            st["msg"] = mid
        st["msg_tokens"] = ctx
        st["context"] = ctx
        return
    if b'"type":"ai-title"' in line or b'"type":"custom-title"' in line:
        rec = _loads(line)
        if isinstance(rec, dict):
            if rec.get("customTitle"):
                st["custom"] = rec["customTitle"]
            if rec.get("aiTitle"):
                st["ai"] = rec["aiTitle"]
        return
    if b'"type":"user"' in line and b'"tool_result"' not in line and b'"isMeta":true' not in line:
        rec = _loads(line)
        if not isinstance(rec, dict) or rec.get("type") != "user" or rec.get("isSidechain"):
            return
        text = base.message_text(rec.get("message")).strip()
        if not text or text.startswith("<"):
            return
        st["prompts"] += 1
        base.count_day(st["days"], rec.get("timestamp"))
        if st["first_msg"] is None:
            st["first_msg"] = text[:400]


def _finish(st):
    out = {k: st[k] for k in ("cwd", "first", "last", "branch", "ai", "custom",
                              "first_msg", "prompts", "ext_parent", "context")}
    out["days"] = dict(st["days"])
    out["uuids"] = frozenset(st["uuids"])
    out["tokens"] = st["total"] + st["msg_tokens"]
    return out


def _registered():
    """(session id, pid, record) for each well-formed entry of Claude's process registry."""
    for path in glob.glob(os.path.join(glob.escape(CLAUDE_LIVE), "*.json")):
        try:
            with open(path, "rb") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict):
            continue
        sid, pid = rec.get("sessionId"), rec.get("pid")
        if isinstance(sid, str) and sid and type(pid) is int and pid > 0:
            yield sid, pid, rec


def live_sessions():
    """{session id: {"status": str}} for registered Claude processes still running."""
    live = {}
    for sid, pid, rec in _registered():
        if base.pid_alive(pid):
            status = rec.get("status")
            live[sid] = {"status": status if isinstance(status, str) and status else "running"}
    return live


def encode(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def project_dir(cwd):
    name = encode(cwd)
    if len(name) > 200:
        raise OpError("target path too long for Claude's project-dir naming")
    return os.path.join(CLAUDE_PROJECTS, name)


def statusline_installed():
    try:
        with open(os.path.join(CLAUDE_DIR, "settings.json"), "rb") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        return False
    sl = cfg.get("statusLine") if isinstance(cfg, dict) else None
    cmd = sl.get("command") if isinstance(sl, dict) else None
    if not isinstance(cmd, str):
        return False
    try:
        words = shlex.split(cmd)
    except ValueError:
        return False
    return len(words) >= 2 and words[-1] == "--statusline" and os.path.basename(words[-2]) == "atree.py"


class Claude(Agent):
    name = "claude"
    label = "Claude"
    color = "#da724b"
    can_move = True
    new_command = "claude"
    continue_command = "claude --continue"

    def resume_command(self, s):
        return "claude --resume %s" % s["id"]

    def live_pid(self, s):
        return next((pid for sid, pid, _ in _registered() if sid == s["id"] and base.pid_alive(pid)), None)

    def ask_argv(self):
        return ["claude", "-p", "--no-session-persistence", "--model", "haiku"]

    def scan(self):
        if not os.path.isdir(CLAUDE_PROJECTS):
            return []
        live = live_sessions()
        parsed = []
        for proj in os.scandir(CLAUDE_PROJECTS):
            if not proj.is_dir():
                continue
            for path in glob.glob(os.path.join(glob.escape(proj.path), "*.jsonl")):
                try:
                    info, st = base.cached_lines(path, _init, _step, _finish)
                except OSError:
                    continue
                sid = os.path.basename(path)[:-6]
                if info["first_msg"] is None and info["ai"] is None and sid not in live:
                    continue
                parsed.append((sid, path, proj.name, info, st))
        owner = {}
        for sid, _p, _n, info, _st in parsed:
            for u in info["uuids"]:
                owner.setdefault(u, sid)
        talks = []
        for sid, path, projname, info, st in parsed:
            companion = path[:-6]
            try:
                agents = len(glob.glob(os.path.join(glob.escape(companion), "subagents", "*.jsonl")))
            except OSError:
                agents = 0
            prev = owner.get(info["ext_parent"])
            related = [{"key": "claude:" + prev, "kind": "continued"}] if prev and prev != sid else []
            lv = live.get(sid)
            talks.append(base.talk(
                "claude", sid,
                cwd=info["cwd"] or projname.replace("-", "/"),
                title=base.pick_title(info["custom"], info["ai"], (info["first_msg"] or "")[:80], sid=sid),
                first_msg=info["first_msg"] or "",
                created=base.iso_to_epoch(info["first"]) or st.st_mtime,
                updated=max(base.iso_to_epoch(info["last"]) or 0, st.st_mtime),
                prompts=info["prompts"], days=info["days"], branch=info["branch"] or None,
                size=st.st_size, agents=agents, path=path, related=related,
                live=lv is not None, live_status=lv["status"] if lv else None,
                context=info["context"], tokens=info["tokens"]))
        return talks

    def move(self, s, target):
        src = s["path"]
        companion = src[:-6] if src.endswith(".jsonl") else src + ".d"
        dst_dir = project_dir(target)
        dst = os.path.join(dst_dir, os.path.basename(src))
        dst_companion = dst[:-6] if dst.endswith(".jsonl") else dst + ".d"
        if dst != src:
            for p in (dst, dst_companion):
                if os.path.lexists(p):
                    raise OpError("%s already exists" % p)
        base.backup([src, companion], "move-" + s["id"][:8])
        os.makedirs(dst_dir, exist_ok=True)
        base.rewrite_jsonl(src, dst, s["cwd"], target)
        if dst != src:
            has_companion = os.path.isdir(companion)
            renamed = False
            try:
                if has_companion:
                    try:
                        os.rename(companion, dst_companion)
                        renamed = True
                    except OSError as e:
                        if e.errno != errno.EXDEV:
                            raise
                        if os.path.islink(companion):
                            os.symlink(os.readlink(companion), dst_companion)
                        else:
                            shutil.copytree(companion, dst_companion, symlinks=True)
                os.unlink(src)
            except BaseException:
                os.unlink(dst)
                if renamed:
                    os.rename(dst_companion, companion)
                elif os.path.islink(dst_companion):
                    os.unlink(dst_companion)
                elif os.path.isdir(dst_companion):
                    shutil.rmtree(dst_companion)
                raise
            if has_companion and not renamed:
                # The transcript has moved; leftover cross-device originals are best-effort.
                try:
                    if os.path.islink(companion):
                        os.unlink(companion)
                    else:
                        shutil.rmtree(companion)
                except OSError:
                    pass

    def archive_members(self, s):
        members = [s["path"]]
        companion = s["path"][:-6]
        if s["path"].endswith(".jsonl") and os.path.isdir(companion):
            members.append(companion)
        return CLAUDE_PROJECTS, members

    def usage(self):
        from .. import statusline
        saved = statusline.limits()
        if not saved:
            if statusline_installed():
                return {"error": "waiting for Claude Code's status line"}
            return {"error": "run atree.py --install-statusline"}
        bars = []
        now = time.time()
        limits = saved.get("rate_limits") or {}
        for key, label, window in (("five_hour", "session (5h)", 18000), ("seven_day", "week", 604800)):
            b = limits.get(key) if isinstance(limits, dict) else None
            if not isinstance(b, dict):
                continue
            used = b.get("used_percentage")
            if type(used) not in (int, float):
                continue
            resets = b.get("resets_at")
            if isinstance(resets, str):
                resets = base.iso_to_epoch(resets)
            elif type(resets) not in (int, float):
                resets = None
            stale = bool(resets) and resets <= now
            bars.append({"label": label, "used": 0.0 if stale else float(used),
                         "window": window, "resets_at": resets, "stale": stale})
        return {"bars": bars, "observed_at": saved.get("at")}
