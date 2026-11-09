"""Mutating operations on talks: move, archive, restore, delete; terminals."""
import contextlib
import fcntl
import gzip
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import threading
import time
import zlib

from .agents import BY_NAME, OpError
from .agents import base
from . import reattach
from .fsutil import atomic_write
from .scan import scan_all

DATA = base.DATA
ARCHIVE_DIR = os.path.join(DATA, "archive")
ARCHIVE_INDEX = os.path.join(ARCHIVE_DIR, "index.json")
TASKS_FILE = os.path.join(DATA, "tasks.json")

_op_lock = threading.Lock()


# ---------------------------------------------------------------- locks, json

@contextlib.contextmanager
def _flock(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


@contextlib.contextmanager
def op_lock():
    with _op_lock, _flock(os.path.join(DATA, "ops.lock")):
        yield


def tasks_lock():
    return _flock(os.path.join(os.path.dirname(TASKS_FILE), "tasks.lock"))


def _load(path, default):
    try:
        with open(path, "rb") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    atomic_write(path, json.dumps(data, indent=1))


def archived():
    return _load(ARCHIVE_INDEX, {})


def _find(key):
    for s in scan_all():
        if s["key"] == key:
            return s
    raise OpError("session %s not found" % key)


def _agent(name):
    try:
        return BY_NAME[name]
    except KeyError:
        raise OpError("unknown agent '%s'" % name) from None


# ---------------------------------------------------------------- move

def move(key, target):
    target = os.path.abspath(os.path.expanduser(target))
    if not os.path.isdir(target):
        raise OpError("%s is not a directory" % target)
    with op_lock():
        s = _find(key)
        agent = _agent(s["tool"])
        if not agent.can_move:
            raise OpError("moving %s talks isn't supported" % agent.label)
        if s["live"]:
            raise OpError("'%s' is running; close it before moving" % s["title"])
        if s["cwd"] == target:
            return "unchanged"
        agent.move(s, target)
        return target


# ---------------------------------------------------------------- archive

def _snapshot(base_dir, members):
    """{relative path: (mode, dev, ino, size, mtime_ns, ctime_ns)} without following links."""
    snap = {}

    def visit(path):
        rel = os.path.relpath(path, base_dir)
        st = os.lstat(path)
        if not (os.path.stat.S_ISREG(st.st_mode) or os.path.stat.S_ISDIR(st.st_mode)):
            raise OpError("unsafe archive member: %s" % rel)
        snap[rel] = (st.st_mode, st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if os.path.stat.S_ISDIR(st.st_mode):
            for name in sorted(os.listdir(path)):
                visit(os.path.join(path, name))

    for m in members:
        visit(m)
    return snap


def _verify(path):
    """Read the whole archive (checking the gzip trailer); return its members."""
    try:
        with gzip.open(path, "rb") as gz:
            with tarfile.open(fileobj=gz, mode="r|") as tar:
                members = []
                for m in tar:
                    members.append(m)
                    if m.isfile():
                        f = tar.extractfile(m)
                        while f.read(1 << 20):
                            pass
            while gz.read(1 << 20):
                pass
    except (OSError, EOFError, zlib.error, tarfile.TarError) as e:
        raise OpError("archive verification failed: %s" % e) from e
    for m in members:
        if not (m.isfile() or m.isdir()):
            raise OpError("unsafe archive member: %s" % m.name)
    return members


def _archive_name(s):
    stem = re.sub(r"[^\w.-]", "_", "%s-%s" % (s["tool"], s["id"]))
    out = os.path.join(ARCHIVE_DIR, stem + ".tar.gz")
    n = 2
    while os.path.lexists(out):
        out = os.path.join(ARCHIVE_DIR, "%s-%d.tar.gz" % (stem, n))
        n += 1
    return out


def _remove(path):
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path)
    else:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass


def archive(key):
    with op_lock():
        s = _find(key)
        agent = _agent(s["tool"])
        if not agent.can_archive:
            raise OpError("archiving %s talks isn't supported" % agent.label)
        if s["live"]:
            raise OpError("'%s' is running; close it before archiving" % s["title"])
        index = archived()
        if key in index:
            raise OpError("%s is already archived; restore or delete that copy first" % key)
        base_dir, members = agent.archive_members(s)
        before = _snapshot(base_dir, members)
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        out = _archive_name(s)
        tmp = out + ".tmp"
        try:
            with open(tmp, "wb") as raw:
                with tarfile.open(fileobj=raw, mode="w:gz") as tar:
                    for m in members:
                        tar.add(m, arcname=os.path.relpath(m, base_dir))
                raw.flush()
                os.fsync(raw.fileno())
            packed = {m.name: m.size for m in _verify(tmp) if m.isfile()}
            files = {rel: v[3] for rel, v in before.items() if os.path.stat.S_ISREG(v[0])}
            if packed != files or _snapshot(base_dir, members) != before:
                raise OpError("archive of %s doesn't match its files; nothing was removed" % key)
            os.rename(tmp, out)
            index[key] = dict(s, archive=out, base=base_dir, archived_at=time.time(),
                              orig_size=s["size"], size=os.path.getsize(out))
            _save(ARCHIVE_INDEX, index)
            try:
                agent.after_archive(s)
            except BaseException:
                index.pop(key, None)
                _save(ARCHIVE_INDEX, index)
                raise
        except BaseException:
            for p in (tmp, out):
                try:
                    os.unlink(p)
                except OSError:
                    pass
            raise
        try:
            for m in members:
                _remove(m)
        except OSError as e:
            raise OpError("archived %s to %s, but removing the originals failed: %s" % (key, out, e)) from e
        return out


# ---------------------------------------------------------------- restore

def _check_member(m, base_dir):
    name = m.name
    safe = (m.isfile() or m.isdir()) and os.path.normpath(name) == name.rstrip("/") \
        and not name.startswith(("/", "..")) and name not in (".", "")
    if not safe:
        raise OpError("unsafe archive member: %s" % name)
    dest = os.path.join(base_dir, name)
    if os.path.lexists(dest):
        raise OpError("%s already exists" % dest)
    return dest


def restore(key):
    with op_lock():
        index = archived()
        entry = index.get(key)
        if entry is None:
            raise OpError("%s is not archived" % key)
        agent = _agent(entry["tool"])
        base_dir = entry.get("base") or agent.archive_members(entry)[0]
        try:
            members = _verify(entry["archive"])
            dests = [_check_member(m, base_dir) for m in members]
            created = []
            try:
                os.makedirs(base_dir, exist_ok=True)
                with tarfile.open(entry["archive"], "r:gz") as tar:
                    for m, dest in zip(members, dests):
                        created.append(dest)
                        tar.extract(m, base_dir, filter="data")
                agent.after_restore(entry)
            except BaseException:
                for dest in reversed(created):
                    try:
                        if os.path.isdir(dest) and not os.path.islink(dest):
                            os.rmdir(dest)
                        else:
                            os.unlink(dest)
                    except OSError:
                        pass
                raise
        except OpError:
            raise
        except (OSError, tarfile.TarError) as e:
            raise OpError("restoring %s failed: %s" % (key, e)) from e
        index.pop(key, None)
        _save(ARCHIVE_INDEX, index)
        try:
            os.unlink(entry["archive"])
        except FileNotFoundError:
            pass
        return entry["path"]


def delete_archived(key):
    with op_lock():
        index = archived()
        entry = index.pop(key, None)
        if entry is None:
            raise OpError("%s is not archived" % key)
        _save(ARCHIVE_INDEX, index)
        try:
            os.unlink(entry["archive"])
        except FileNotFoundError:
            pass
        return entry["archive"]


# ---------------------------------------------------------------- terminals

def resume_command(key, s=None):
    s = s or _find(key)
    if not re.fullmatch(r"[\w.:-]+", s["id"]):
        raise OpError("%s: unexpected characters in the session id" % key)
    return s["cwd"], _agent(s["tool"]).resume_command(s)


def open_terminal(key):
    """Resume a talk; one still running is shown where it runs instead of started twice."""
    s = _find(key)
    pid = _agent(s["tool"]).live_pid(s) if s.get("live") else None
    if pid:
        cwd = s["cwd"] if os.path.isdir(s["cwd"]) else base.HOME
        status = reattach.reattach(pid, s["title"], lambda cmd: run_in_terminal(cwd, cmd))
        if status:
            return status
    run_in_terminal(*resume_command(key, s))
    return "opening terminal…"


def _gsetting(key):
    try:
        out = subprocess.run(["gsettings", "get", "org.gnome.desktop.default-applications.terminal", key],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    v = out.stdout.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
        v = v[1:-1]
    return v


def _default_terminal():
    exe = _gsetting("exec")
    if not exe or not shutil.which(exe):
        return None
    arg = _gsetting("exec-arg")
    return [exe] + ([arg] if arg else [])


def _real_name(exe):
    """The program an alternatives link like x-terminal-emulator points to."""
    path = shutil.which(exe) or exe
    name = os.path.basename(os.path.realpath(path))
    return name[:-8] if name.endswith(".wrapper") else name


def _known(name, cwd, argv, script):
    if name == "ptyxis":
        return ["ptyxis", "--new-window", "--working-directory", cwd, "--"] + argv
    if name == "gnome-terminal":
        return ["gnome-terminal", "--working-directory=" + cwd, "--"] + argv
    if name == "terminator":
        # one string: a running Terminator forwards it over D-Bus and splits a list badly
        return ["terminator", "--working-directory=" + cwd, "--command", shlex.join(argv)]
    return None


def shell_path(timeout=5):
    """PATH as the user's interactive shell sets it, or None if that can't be read."""
    shell = os.environ.get("SHELL") or "/bin/bash"
    marker = "__atree_path__"
    try:
        out = subprocess.run([shell, "-i", "-c", "printf '%s%%s' \"$PATH\"" % marker],
                             capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    _, found, path = out.stdout.rpartition(marker)
    return path.strip() if found and path.strip() else None


def adopt_shell_path():
    """Add the interactive shell's PATH entries we lack: a session started from GNOME (the
    widget) never read ~/.zshrc, so agent CLIs installed there weren't found."""
    path = shell_path()
    if not path:
        return
    have = os.environ.get("PATH", "").split(os.pathsep)
    extra = [p for p in path.split(os.pathsep) if p and p not in have]
    if extra:
        os.environ["PATH"] = os.pathsep.join(extra + have)


def run_in_terminal(cwd, cmd):
    if not os.path.isdir(cwd):
        raise OpError("%s no longer exists" % cwd)
    shell = os.environ.get("SHELL") or "/bin/bash"
    script = "%s; exec %s" % (cmd, shlex.quote(shell))
    # interactive, so the user's rc file sets PATH (agent CLIs often live in ~/.local/bin)
    argv = [shell, "-i", "-c", script]
    candidates = []
    default = _default_terminal()
    if default:
        candidates.append(_known(_real_name(default[0]), cwd, argv, script) or default + argv)
    fallback = shutil.which("x-terminal-emulator")
    candidates += [(_known(_real_name(fallback), cwd, argv, script) if fallback else None)
                   or ["x-terminal-emulator", "-e"] + argv,
                   _known("ptyxis", cwd, argv, script),
                   _known("gnome-terminal", cwd, argv, script)]
    for c in candidates:
        if shutil.which(c[0]):
            proc = subprocess.Popen(c, cwd=cwd, stdin=subprocess.DEVNULL, start_new_session=True)
            threading.Thread(target=proc.wait, daemon=True).start()
            return
    raise OpError("no terminal emulator found")
