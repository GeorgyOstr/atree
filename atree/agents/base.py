"""Shared adapter machinery: the Agent interface, parse caches, path rewrites."""
import copy
import datetime as _dt
import json
import os
import shutil
import threading
import time


def _env(name, default):
    """An environment variable, with set-but-empty treated as unset."""
    return os.environ.get(name) or default


HOME = os.path.expanduser("~")
DATA = os.path.join(_env("XDG_DATA_HOME", os.path.join(HOME, ".local", "share")), "atree")
BACKUPS = os.path.join(DATA, "backups")

TAIL = 64  # bytes of fingerprint kept before the committed offset


class OpError(Exception):
    """A user-facing operation failure; the message is shown as is."""


class Agent:
    name = ""
    label = ""
    color = "#8a93a6"
    can_move = False
    can_archive = True
    new_command = None
    continue_command = None

    def scan(self):
        return []

    def resume_command(self, s):
        raise OpError("%s sessions can't be resumed from here" % self.label)

    def live_pid(self, s):
        """The process running session ``s``, or None if none is found."""
        return session_pid(s)

    def ask_argv(self):
        return None

    def move(self, s, target):
        raise OpError("moving %s sessions isn't supported" % self.label)

    def archive_members(self, s):
        return os.path.dirname(s["path"]), [s["path"]]

    def after_archive(self, s):
        pass

    def after_restore(self, entry):
        pass

    def usage(self):
        return None


# ---------------------------------------------------------------- small helpers

def tildify(path):
    if path == HOME:
        return "~"
    if path.startswith(HOME + "/"):
        return "~" + path[len(HOME):]
    return path


def fmt_tokens(n):
    if not n:
        return "0"
    if n >= 1_000_000:
        return "%.1fM" % (n / 1_000_000)
    if n >= 1000:
        return "%dk" % round(n / 1000)
    return str(int(n))


def is_timestamp(v):
    if type(v) not in (int, float):
        return False
    try:
        _dt.datetime.fromtimestamp(v)
    except (OverflowError, OSError, ValueError):
        return False
    return True


def iso_to_epoch(ts):
    if not ts or not isinstance(ts, str):
        return None
    if ts.endswith(("Z", "z")):
        ts = ts[:-1] + "+00:00"
    try:
        return _dt.datetime.fromisoformat(ts).timestamp()
    except (ValueError, OverflowError, OSError):
        return None


def local_day(epoch):
    return time.strftime("%Y-%m-%d", time.localtime(epoch))


def message_text(msg):
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                text = part.get("text")
                return text if isinstance(text, str) else ""
    return ""


def count_day(days, iso_ts):
    t = iso_to_epoch(iso_ts)
    if t:
        day = local_day(t)
        days[day] = days.get(day, 0) + 1


def pid_alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (OverflowError, ValueError):
        return False
    except OSError:
        return False
    return True


PROC = "/proc"


def session_pid(s):
    """Another process that was started with the session id as an argument (a resume) or
    keeps the transcript open: how agents without a process registry are found."""
    path = s.get("path")
    try:
        pids = [int(d) for d in os.listdir(PROC) if d.isdigit() and int(d) != os.getpid()]
    except OSError:
        return None
    for pid in pids:
        try:
            with open(os.path.join(PROC, str(pid), "cmdline"), "rb") as f:
                args = f.read().decode("utf-8", "replace").split("\0")[1:]
        except OSError:
            continue
        if any(a == s["id"] or a.endswith("=" + s["id"]) for a in args):
            return pid
        fd_dir = os.path.join(PROC, str(pid), "fd")
        try:
            fds = os.listdir(fd_dir) if path else []
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink(os.path.join(fd_dir, fd)) == path:
                    return pid
            except OSError:
                pass
    return None


def stat_key(st):
    return (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)


def talk(agent, sid, **fields):
    """A talk dict with every key present."""
    t = {
        "key": "%s:%s" % (agent, sid), "tool": agent, "id": sid, "cwd": "/",
        "title": sid[:8], "first_msg": "", "created": 0.0, "updated": 0.0,
        "prompts": 0, "days": {}, "branch": None, "size": 0, "agents": 0,
        "path": "", "related": [], "parent": None, "live": False,
        "live_status": None, "context": 0, "tokens": 0,
    }
    t.update(fields)
    # third-party files can hold anything; the UI relies on these being strings
    if not isinstance(t["cwd"], str) or not t["cwd"]:
        t["cwd"] = "/"
    if not isinstance(t["title"], str) or not t["title"]:
        t["title"] = sid[:8]
    if not isinstance(t["first_msg"], str):
        t["first_msg"] = ""
    if t["branch"] is not None and not isinstance(t["branch"], str):
        t["branch"] = str(t["branch"])
    return t


def pick_title(*candidates, sid, n=8):
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
    return sid[:n]


# ---------------------------------------------------------------- parse caches

_cache = {}
_lines_cache = {}
_cache_lock = threading.Lock()


def cached(path, parser):
    """``(parser(path), stat)``, re-parsed only when the file changes."""
    path = os.fspath(path)
    st = os.stat(path)
    key = stat_key(st)
    hit = _cache.get(path)
    if hit is not None and hit[0] == key:
        return hit[1], st
    info = parser(path)
    _cache[path] = (key, info)
    return info, st


class _Lines:
    __slots__ = ("lock", "state", "offset", "tail", "dev", "ino", "mtime", "key", "result")

    def __init__(self):
        self.lock = threading.Lock()
        self.state = None
        self.offset = 0
        self.tail = b""
        self.dev = self.ino = None
        self.mtime = 0
        self.key = None
        self.result = None


def cached_lines(path, init, step, finish):
    """Incrementally parse an append-only line file; see spec 2.3."""
    path = os.fspath(path)
    with _cache_lock:
        entry = _lines_cache.get(path)
        if entry is None:
            entry = _lines_cache[path] = _Lines()
    with entry.lock:
        st = os.stat(path)
        key = stat_key(st)
        if key == entry.key:
            return entry.result, st

        def restart():
            entry.state = init()
            entry.offset = 0
            entry.tail = b""
            entry.dev, entry.ino = st.st_dev, st.st_ino

        if (entry.state is None or (entry.dev, entry.ino) != (st.st_dev, st.st_ino)
                or st.st_size < entry.offset or st.st_mtime_ns < entry.mtime):
            restart()
        entry.mtime = st.st_mtime_ns
        fragment = b""
        with open(path, "rb") as f:
            if entry.tail:
                f.seek(entry.offset - len(entry.tail))
                if f.read(len(entry.tail)) != entry.tail:
                    restart()
            f.seek(entry.offset)
            state = entry.state
            for line in f:
                if not line.endswith(b"\n"):
                    fragment = line
                    break
                try:
                    step(state, line[:-1])
                except Exception as e:  # one bad line must not stop the rest
                    print("atree: %s: skipped a line: %r" % (path, e))
                entry.offset += len(line)
                entry.tail = (entry.tail + line)[-TAIL:]
        result = None
        if fragment.strip():
            try:
                json.loads(fragment)
            except ValueError:
                pass
            else:
                peek = copy.deepcopy(state)
                try:
                    step(peek, fragment)
                    result = finish(peek)
                except Exception:
                    result = None
        if result is None:
            result = finish(state)
        entry.key = key
        entry.result = result
        return result, st


def prune_caches():
    for d in (_cache, _lines_cache):
        with _cache_lock:
            dead = [p for p in list(d) if not os.path.exists(p)]
            for p in dead:
                d.pop(p, None)


# ---------------------------------------------------------------- moving files

def backup(paths, label):
    dest = os.path.join(BACKUPS, "%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), label))
    os.makedirs(dest, exist_ok=True)
    for p in paths:
        p = os.fspath(p)
        target = os.path.join(dest, os.path.basename(p.rstrip("/")))
        if os.path.isdir(p):
            shutil.copytree(p, target, dirs_exist_ok=True, symlinks=True)
        elif os.path.isfile(p):
            shutil.copy2(p, target)
    return dest


def remap(value, old, new):
    if value == old:
        return new
    o = old.rstrip("/")
    if value.startswith(o + "/"):
        return new.rstrip("/") + value[len(o):]
    return value


PATH_KEYS = ("cwd", "workspace_roots", "runtime_workspace_roots")


def remap_tree(obj, old, new):
    changed = False
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in PATH_KEYS and isinstance(v, str):
                nv = remap(v, old, new)
                if nv != v:
                    obj[k] = nv
                    changed = True
            elif k in PATH_KEYS and isinstance(v, list):
                for i, item in enumerate(v):
                    if isinstance(item, str):
                        ni = remap(item, old, new)
                        if ni != item:
                            v[i] = ni
                            changed = True
            elif isinstance(v, (dict, list)):
                changed |= remap_tree(v, old, new)
    elif isinstance(obj, list):
        for v in obj:
            if isinstance(v, (dict, list)):
                changed |= remap_tree(v, old, new)
    return changed


def rewrite_jsonl(src, dst, old, new):
    """Stream ``src`` into ``dst`` (possibly the same file) with cwd paths remapped."""
    src, dst = os.fspath(src), os.fspath(dst)
    st = os.stat(src)
    folder = os.path.dirname(dst) or "."
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    while True:
        tmp = os.path.join(folder, "%s.%s.atree-tmp" % (os.path.basename(dst), os.urandom(6).hex()))
        try:
            fd = os.open(tmp, flags, 0o600)
            break
        except FileExistsError:
            continue
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as out, open(src, "rb") as f:
            for line in f:
                if b'"cwd"' in line or b"workspace_roots" in line:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        obj = None
                    if obj is not None and remap_tree(obj, old, new):
                        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode() + b"\n"
                out.write(line)
            out.flush()
            os.fsync(out.fileno())
            os.fchmod(out.fileno(), st.st_mode & 0o7777)
        os.utime(tmp, ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
