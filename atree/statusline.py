"""Claude Code status-line hook: saves rate limits for the usage bars, then runs the displaced command.

Kept light: ``atree.py --statusline`` runs on every status refresh.
"""
import json
import os
import shlex
import subprocess
import sys
import time

from .fsutil import atomic_write

_HOME = os.path.expanduser("~")
CACHE = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(_HOME, ".cache"), "atree")
DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(_HOME, ".local", "share"), "atree")
LIMITS = os.path.join(CACHE, "claude-limits.json")
NEXT = os.path.join(DATA, "statusline-next.json")
CLAUDE_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(_HOME, ".claude")
SETTINGS = os.path.join(CLAUDE_DIR, "settings.json")
BACKUP = SETTINGS + ".atree-backup"

MISSING = object()


class _Invalid(Exception):
    pass


def _dump(obj):
    return json.dumps(obj, indent=2) + "\n"


def _load(path):
    """Parsed JSON, or MISSING when the file doesn't exist; other failures give None."""
    try:
        with open(path, "rb") as f:
            return json.loads(f.read())
    except FileNotFoundError:
        return MISSING
    except (OSError, ValueError):
        return None


def _strict(path, missing):
    """A JSON object from ``path`` (``missing`` when absent); raises _Invalid otherwise."""
    obj = _load(path)
    if obj is MISSING:
        return missing
    if not isinstance(obj, dict):
        raise _Invalid("%s isn't a valid JSON object: fix it and run this again" % path)
    return obj


def is_ours(command):
    if not isinstance(command, str):
        return False
    try:
        words = shlex.split(command)
    except ValueError:
        return False
    return len(words) >= 2 and words[-1] == "--statusline" and os.path.basename(words[-2]) == "atree.py"


# ---------------------------------------------------------------- hook

def run():
    if os.environ.get("ATREE_STATUSLINE"):
        return 0
    data = sys.stdin.buffer.read() if sys.stdin is not None else b""
    try:
        payload = json.loads(data)
    except ValueError:
        payload = None
    limits = payload.get("rate_limits") if isinstance(payload, dict) else None
    if isinstance(limits, dict) and limits:
        try:
            atomic_write(LIMITS, _dump({"at": time.time(), "rate_limits": limits}), 0o600)
        except OSError:
            pass
    nxt = _load(NEXT)
    command = nxt.get("command") if isinstance(nxt, dict) else None
    if not isinstance(command, str) or not command:
        return 0
    env = dict(os.environ, ATREE_STATUSLINE="1")
    return subprocess.run(command, shell=True, input=data, env=env).returncode


def limits():
    saved = _load(LIMITS)
    if isinstance(saved, dict) and isinstance(saved.get("rate_limits"), dict):
        return saved
    return None


# ---------------------------------------------------------------- install / uninstall

def _read_settings():
    try:
        with open(SETTINGS, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, {}
    try:
        settings = json.loads(raw)
    except ValueError:
        settings = None
    if not isinstance(settings, dict):
        raise _Invalid("%s isn't a valid JSON object: fix it and run this again" % SETTINGS)
    return raw, settings


def install(command):
    """Point Claude Code's status line at ``command``; returns an exit status."""
    try:
        raw, settings = _read_settings()
    except _Invalid as e:
        print(e, file=sys.stderr)
        return 1
    original = settings.get("statusLine", MISSING)
    line = original if isinstance(original, dict) and original.get("type") == "command" else {}
    prev = line.get("command")
    if not is_ours(prev):
        nxt = {"command": prev or None}
        if original is not MISSING:
            nxt["statusLine"] = original
        atomic_write(NEXT, _dump(nxt), 0o600)
        if raw is not None:
            atomic_write(BACKUP, raw, 0o600)
        shown = prev
    else:
        shown = None
    settings["statusLine"] = dict(line, type="command", command=command)
    atomic_write(SETTINGS, _dump(settings), 0o600)
    print("Claude Code's status line now feeds atree's usage bars"
          + (", then runs: %s" % shown if shown else "")
          + "\nthey fill in once a Claude session refreshes its status line")
    return 0


def uninstall():
    """Restore the status line atree displaced; returns an exit status."""
    try:
        _, settings = _read_settings()
        line = settings.get("statusLine")
        if not isinstance(line, dict) or not is_ours(line.get("command")):
            print("atree isn't in Claude Code's status line")
            return 0
        nxt = _strict(NEXT, None)
    except _Invalid as e:
        print(e, file=sys.stderr)
        return 1
    if nxt is not None and "command" in nxt and not (nxt["command"] is None or isinstance(nxt["command"], str)):
        print("%s has an invalid command: fix it and run this again" % NEXT, file=sys.stderr)
        return 1
    prev = (nxt or {}).get("command") or None
    if nxt is not None and "statusLine" in nxt:
        settings["statusLine"] = nxt["statusLine"]
        msg = "restored Claude Code's status line" + (": %s" % prev if prev else "")
    elif prev:
        settings["statusLine"] = dict(line, command=prev)
        msg = "restored Claude Code's status line: %s" % prev
    else:
        backup = _load(BACKUP) if nxt is None else None
        if isinstance(backup, dict) and "statusLine" in backup:
            settings["statusLine"] = backup["statusLine"]
            msg = "restored Claude Code's status line from %s" % BACKUP
        else:
            settings.pop("statusLine", None)
            msg = "removed atree from Claude Code's status line"
            if nxt is None:
                msg += "; %s is missing and %s has none to restore" % (NEXT, BACKUP)
    atomic_write(SETTINGS, _dump(settings), 0o600)
    if nxt is not None:
        try:
            os.unlink(NEXT)
        except FileNotFoundError:
            pass
    print(msg)
    return 0
