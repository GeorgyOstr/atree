"""AI search: ask an agent CLI, headless, which talks fit an idea."""
import json
import os
import signal
import subprocess
import time

from .agents import BY_NAME, OpError
from .agents.base import DATA, tildify

SEARCH_DIR = os.path.join(DATA, "search")
TIMEOUT = 180
MAX_RESULTS = 8

HEADER = """\
Below is a list of the user's coding-agent talks, one per line:
key | agent | folder | last active | title | first prompt

Find the talks that best fit this idea: {query}

Judge by meaning, not just matching words. Answer with only a JSON array of at most \
{n} objects, best first: [{{"key": "<key from the list>", "why": "<under 12 words>"}}]. \
Answer [] if nothing fits. Do not use any tools.
"""


def _collapse(text, n):
    return " ".join((text or "").split())[:n]


def build_prompt(query, candidates):
    lines = [HEADER.format(query=query.strip(), n=MAX_RESULTS)]
    for s in candidates:
        lines.append("%s | %s | %s | %s | %s | %s" % (
            s["key"], s["tool"], tildify(s["cwd"]),
            time.strftime("%Y-%m-%d", time.localtime(s.get("updated") or 0)),
            _collapse(s.get("title"), 80), _collapse(s.get("first_msg"), 160)))
    return "\n".join(lines)


def parse_answer(text):
    """The first JSON list of objects found in ``text``, or None."""
    dec = json.JSONDecoder()
    i = text.find("[")
    while i != -1:
        try:
            value, _end = dec.raw_decode(text, i)
        except ValueError:
            value = None
        if isinstance(value, list) and all(isinstance(v, dict) for v in value):
            return value
        i = text.find("[", i + 1)
    return None


def _last_line(text):
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1].strip() if lines else ""


def search(agent, query, sessions):
    a = BY_NAME.get(agent)
    if a is None:
        raise OpError("unknown agent '%s'" % agent)
    candidates = [s for s in sessions if not s.get("archived")]
    argv = a.ask_argv()
    if argv is None:
        raise OpError("%s can't answer questions headless" % a.label)
    os.makedirs(SEARCH_DIR, exist_ok=True)
    try:
        proc = subprocess.Popen(argv, cwd=SEARCH_DIR, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                errors="replace", start_new_session=True)
    except FileNotFoundError:
        raise OpError("%s not found" % argv[0]) from None
    try:
        out, err = proc.communicate(build_prompt(query, candidates), timeout=TIMEOUT)
    except BaseException as e:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.communicate(timeout=5)
        except Exception:
            pass
        proc.wait()
        if isinstance(e, subprocess.TimeoutExpired):
            raise OpError("%s took longer than %ds" % (a.label, TIMEOUT)) from None
        raise
    if proc.returncode:
        reason = _last_line(err) or _last_line(out) or "exit code %d" % proc.returncode
        raise OpError("%s failed: %s" % (a.label, reason))
    items = parse_answer(out)
    if items is None:
        raise OpError("%s: the agent didn't answer with a list" % a.label)
    keys = {s["key"] for s in candidates}
    results, seen = [], set()
    for it in items:
        key = it.get("key")
        if isinstance(key, str) and key in keys and key not in seen:
            seen.add(key)
            why = it.get("why")
            results.append({"key": key, "why": ("" if why is None else str(why))[:120]})
            if len(results) == MAX_RESULTS:
                break
    return results
