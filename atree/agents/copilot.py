"""GitHub Copilot CLI: ~/.copilot/session-state/<id>/{events.jsonl,workspace.yaml}."""
import glob
import json
import os

from . import base
from .base import Agent

COPILOT_DIR = base._env("COPILOT_HOME", os.path.join(base.HOME, ".copilot"))
STATE = os.path.join(COPILOT_DIR, "session-state")

def _scalar(v):
    if v in ("", "~", "null"):
        return None
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return json.loads(v)
        except ValueError:
            return v[1:-1]
    return v


def parse_yaml(text):
    """The flat ``key: value`` subset Copilot writes."""
    out = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line or line[0] in "#- \t" or ":" not in line:
            continue
        key, value = (p.strip() for p in line.split(":", 1))
        if value[:1] in ("|", ">"):
            block = []
            while i < len(lines) and (not lines[i].strip() or lines[i][0] in " \t"):
                block.append(lines[i].strip())
                i += 1
            v = ("\n" if value[0] == "|" else " ").join(block).strip() or None
        else:
            v = _scalar(value)
        if v is not None:  # a null leaves an earlier value of the key alone
            out[key] = v
    return out


def _read_ws(path):
    try:
        with open(path, "rb") as f:
            return parse_yaml(f.read().decode("utf-8", "replace"))
    except OSError:
        return {}


def _workspace(folder):
    path = os.path.join(folder, "workspace.yaml")
    try:
        return base.cached(path, _read_ws)[0]
    except OSError:
        return {}


def _init():
    return {"first": None, "last": None, "start": None, "prompts": 0, "days": {},
            "first_msg": "", "cwd": None, "branch": None, "tokens": 0}


def _sum_tokens(obj):
    total = 0
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("inputTokens", "outputTokens") and type(v) in (int, float):
                total += v
            else:
                total += _sum_tokens(v)
    elif isinstance(obj, list):
        total += sum(_sum_tokens(v) for v in obj)
    return total


def _step(st, line):
    if b'"user.message"' not in line and b'"session.' not in line:
        return
    try:
        rec = json.loads(line)
    except ValueError:
        return
    if not isinstance(rec, dict):
        return
    ts = rec.get("timestamp")
    if isinstance(ts, str):
        st["first"] = st["first"] or ts
        st["last"] = ts
    kind = rec.get("type")
    data = rec.get("data") if isinstance(rec.get("data"), dict) else {}
    if kind == "user.message":
        if rec.get("agentId") or data.get("source") == "system" or data.get("isAutopilotContinuation"):
            return
        st["prompts"] += 1
        base.count_day(st["days"], ts)
        content = data.get("content")
        if not st["first_msg"] and isinstance(content, str) and content.strip():
            st["first_msg"] = content.strip()[:400]
        return
    context = None
    if kind in ("session.start", "session.resume"):
        context = data.get("context")
        if kind == "session.start" and isinstance(data.get("startTime"), str) and not st["start"]:
            st["start"] = data["startTime"]
    elif kind == "session.context_changed":
        context = data
    elif kind == "session.shutdown":
        total = _sum_tokens(data.get("modelMetrics"))
        if total:
            st["tokens"] = total
    if isinstance(context, dict):
        for k in ("cwd", "branch"):
            if context.get(k):
                st[k] = context[k]


def _finish(st):
    out = dict(st)
    out["days"] = dict(st["days"])
    return out


def _start_time(pid):
    """Process start as epoch seconds, or None if unknown."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            fields = f.read().rsplit(")", 1)[1].split()
        ticks = int(fields[19])  # field 22 counted from the pid
        with open("/proc/stat") as f:
            btime = next(int(l.split()[1]) for l in f if l.startswith("btime"))
        return btime + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return None


def running(folder):
    return running_pid(folder) is not None


def running_pid(folder):
    for lock in glob.glob(os.path.join(glob.escape(folder), "inuse.*.lock")):
        parts = os.path.basename(lock).split(".")
        try:
            pid = int(parts[1])
            written = os.path.getmtime(lock)
        except (ValueError, IndexError, OSError):
            continue
        if pid <= 0 or not base.pid_alive(pid):
            continue
        started = _start_time(pid)
        if started is None or started <= written + 2:
            return pid
    return None


class Copilot(Agent):
    name = "copilot"
    label = "Copilot"
    color = "#b08a14"
    new_command = "copilot"
    continue_command = "copilot --continue"

    def resume_command(self, s):
        return "copilot --resume=%s" % s["id"]

    def live_pid(self, s):
        return running_pid(os.path.dirname(s["path"]))

    def scan(self):
        if not os.path.isdir(STATE):
            return []
        talks = []
        for d in os.scandir(STATE):
            events = os.path.join(d.path, "events.jsonl")
            if not os.path.isfile(events):
                continue
            try:
                ev, st = base.cached_lines(events, _init, _step, _finish)
            except OSError:
                continue
            if not ev["prompts"]:
                continue
            ws = _workspace(d.path)
            sid = ws.get("id") or d.name
            live = running(d.path)
            talks.append(base.talk(
                "copilot", sid, cwd=ws.get("cwd") or ev["cwd"] or "/",
                title=base.pick_title(ws.get("name"), ws.get("summary"), ev["first_msg"][:80], sid=sid),
                first_msg=ev["first_msg"],
                created=base.iso_to_epoch(ws.get("created_at") or ev["start"] or ev["first"]) or st.st_mtime,
                updated=max(base.iso_to_epoch(ws.get("updated_at") or ev["last"]) or 0, st.st_mtime),
                prompts=ev["prompts"], days=ev["days"], branch=ws.get("branch") or ev["branch"],
                size=st.st_size, path=events, live=live, live_status="running" if live else None,
                tokens=ev["tokens"]))
        return talks

    def archive_members(self, s):
        return STATE, [os.path.dirname(s["path"])]
