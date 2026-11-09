"""The cleanup assistant: a coding agent opened in its own folder with a tree snapshot."""
import os
import shlex
import sys
import threading
import time

from . import ops
from .agents import BY_NAME, OpError
from .agents.base import fmt_tokens, tildify
from .fsutil import atomic_write
from .scan import scan_all

AGENT_DIR = os.path.join(ops.DATA, "agent")
APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "atree.py")

GUIDE = """\
# atree assistant

You help the user keep their coding-agent talks tidy: sessions of Claude Code, Codex,
Gemini CLI, OpenCode and Copilot CLI, grouped by the folder each was started in.
Spot what could be archived or deleted, talks filed under the wrong folder,
duplicates, and follow-up work worth noting as a task. Talk it through first and only
change anything once the user agrees.

## What you can see

- `tree.json` is a snapshot of every talk, rewritten each time the user opens you.
  Per talk: key, agent, folder, title, first_prompt, started, updated, prompts,
  context_tokens, total_tokens, running, archived, parent (the talk that spawned it)
  and file (its transcript, or its archive when archived).
- `tasks.json` is the user's task notepad.
- Read a transcript at its `file` path when you need the details.

## How to change things

Never edit transcripts, archives or atree's own files yourself. Use these commands,
each of which prints what it did:

    ./atree archive KEY...        pack talks away (undo with restore)
    ./atree restore KEY...        bring archived talks back
    ./atree delete KEY...         delete archived talks for good (archived only)
    ./atree move FOLDER KEY...    file talks under another folder (Claude, Codex)
    ./atree task "TEXT"           add a task to the notepad
    ./atree tree                  refresh tree.json

Running talks can't be archived or moved.
"""


def is_own(s):
    cwd = s.get("cwd") or ""
    return cwd == AGENT_DIR or cwd.startswith(AGENT_DIR.rstrip("/") + "/")


def _when(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else None


def _row(s):
    return {
        "key": s["key"], "agent": s["tool"], "folder": s["cwd"], "title": s["title"],
        "first_prompt": (s.get("first_msg") or "")[:300],
        "started": _when(s.get("created")), "updated": _when(s.get("updated")),
        "prompts": s.get("prompts"), "context_tokens": s.get("context"),
        "total_tokens": s.get("tokens"), "running": bool(s.get("live")),
        "archived": bool(s.get("archived")), "parent": s.get("parent"),
        "file": s.get("archive") if s.get("archived") else s.get("path"),
    }


def _collapse(text, n=None):
    text = " ".join((text or "").split())
    return text[:n] if n else text


def status(talks, tasks, written):
    counts = {}
    for t in talks:
        counts[t["agent"]] = counts.get(t["agent"], 0) + 1
    labels = ", ".join("%d %s" % (n, BY_NAME[a].label if a in BY_NAME else a) for a, n in counts.items())
    folders = {}
    for t in talks:
        folders.setdefault(t["folder"], []).append(t)
    lines = [
        "## Current state (written %s; refreshed each time the user opens you)" % written, "",
        "%d talks (%s) in %d folders · %d running · %d archived" % (
            len(talks), labels, len(folders), sum(t["running"] for t in talks),
            sum(t["archived"] for t in talks)), "",
    ]
    tasks = [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []
    done = sum(bool(t.get("done")) for t in tasks)
    lines.append("Tasks: %d open, %d done" % (len(tasks) - done, done))
    lines += ["- [%s] %s" % ("x" if t.get("done") else " ", t.get("text", "")) for t in tasks]
    lines += ["", "### Talks by folder", "",
              "One line each, newest first: key · title · last active · prompts · tokens used · flags.", ""]
    order = sorted(folders, key=lambda f: max(t["updated"] or "" for t in folders[f]), reverse=True)
    for f in order:
        group = sorted(folders[f], key=lambda t: t["updated"] or "", reverse=True)
        lines.append("#### %s (%d)" % (tildify(f), len(group)))
        for t in group:
            flags = (["RUNNING"] if t["running"] else []) + (["archived"] if t["archived"] else []) \
                + (["sub of %s" % t["parent"]] if t["parent"] else [])
            line = "- %s · %s · %s · %dp · %s" % (t["key"], _collapse(t["title"], 80), t["updated"] or "?",
                                                 t["prompts"] or 0, fmt_tokens(t["total_tokens"]))
            lines.append(line + (" · " + " ".join(flags) if flags else ""))
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def _link_tasks():
    link = os.path.join(AGENT_DIR, "tasks.json")
    target = os.path.abspath(ops.TASKS_FILE)
    try:
        if os.readlink(link) == target:
            return
    except OSError:
        pass
    tmp = os.path.join(AGENT_DIR, ".tasks.json.%d.%d.tmp" % (os.getpid(), threading.get_ident()))
    try:
        os.symlink(target, tmp)
        os.replace(tmp, link)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_context(sessions=None):
    os.makedirs(AGENT_DIR, exist_ok=True)
    if sessions is None:
        sessions = scan_all() + [dict(e, key=k, archived=True, live=False) for k, e in ops.archived().items()]
    own = [s for s in sessions if not is_own(s)]
    talks = [_row(s) for s in sorted(own, key=lambda s: (s["cwd"], -(s.get("updated") or 0)))]
    written = _when(time.time())
    ops._save(os.path.join(AGENT_DIR, "tree.json"), {"written": written, "talks": talks})
    _link_tasks()
    text = GUIDE + "\n" + status(talks, ops._load(ops.TASKS_FILE, []), written)
    for name in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        atomic_write(os.path.join(AGENT_DIR, name), text, 0o644)
    launcher = "#!/bin/sh\nexec %s %s --op \"$@\"\n" % (shlex.quote(sys.executable), shlex.quote(os.path.abspath(APP)))
    atomic_write(os.path.join(AGENT_DIR, "atree"), launcher, 0o755)


def open_terminal(agent, sessions):
    a = BY_NAME.get(agent)
    if a is None or not a.new_command:
        raise OpError("%s can't be the assistant" % agent)
    write_context(sessions)
    resumable = any(s["tool"] == agent and is_own(s) and not s.get("archived") for s in sessions or [])
    ops.run_in_terminal(AGENT_DIR, a.continue_command if resumable else a.new_command)


def cli(args):
    if not args:
        print("usage: atree.py --op archive|restore|delete|move FOLDER|task TEXT|tree ...")
        return 2
    cmd, rest = args[0], list(args[1:])
    if cmd == "tree":
        write_context()
        print("wrote %s" % os.path.join(AGENT_DIR, "tree.json"))
        return 0
    if cmd == "task":
        text = " ".join(rest).strip()
        if not text:
            print("usage: --op task TEXT")
            return 2
        with ops.tasks_lock():
            tasks = ops._load(ops.TASKS_FILE, [])
            if not isinstance(tasks, list):
                tasks = []
            tasks.append({"id": "%x" % time.time_ns(), "text": " ".join(text.split()), "done": False})
            ops._save(ops.TASKS_FILE, tasks)
        print("task added")
        return 0
    if cmd == "move":
        if len(rest) < 2:
            print("usage: --op move FOLDER KEY...")
            return 2
        folder, keys = rest[0], rest[1:]
        run = lambda key: ops.move(key, folder)  # noqa: E731
    else:
        fn = {"archive": ops.archive, "restore": ops.restore, "delete": ops.delete_archived}.get(cmd)
        if fn is None or not rest:
            print("unknown or empty op: %s" % " ".join(args))
            return 2
        keys, run = rest, fn
    failed = 0
    for key in keys:
        if key.split(":", 1)[0] not in BY_NAME:
            print("%s: not a talk key" % key)
            failed += 1
            continue
        try:
            print("%s %s: %s" % (cmd, key, run(key)))
        except Exception as e:
            print("%s %s: FAILED %s" % (cmd, key, e))
            failed += 1
    return 1 if failed else 0
