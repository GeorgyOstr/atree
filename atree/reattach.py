"""Get back to a talk that is still running: raise the terminal window it lives in, or
attach a new terminal to the tmux or screen session that holds it."""
import os
import shlex
import shutil
import subprocess

from .agents import OpError, base

DBUS_PATH = "/org/atree/Desktop"  # exported by gnome-extension/atree@atree


def _read(pid, name):
    with open(os.path.join(base.PROC, str(pid), name), "rb") as f:
        return f.read()


def _parent(pid):
    """(comm, parent pid) of a process, or None once it is gone."""
    try:
        stat = _read(pid, "stat").decode("utf-8", "replace")
    except OSError:
        return None
    head, _, rest = stat.rpartition(")")
    try:
        return head.partition("(")[2], int(rest.split()[1])
    except (IndexError, ValueError):
        return None


def ancestors(pid):
    """[(pid, comm)] of the parents of ``pid``, nearest first, up to but without init."""
    chain, seen = [], {pid}
    info = _parent(pid)
    while info and info[1] > 1 and info[1] not in seen:
        pid = info[1]
        seen.add(pid)
        info = _parent(pid)
        if info:
            chain.append((pid, info[0]))
    return chain


def _environ(pid):
    try:
        raw = _read(pid, "environ").decode("utf-8", "replace")
    except OSError:
        return {}
    return dict(kv.split("=", 1) for kv in raw.split("\0") if "=" in kv)


def _run(argv, timeout=3):
    """Standard output of a helper, or None if it is missing or fails."""
    if not shutil.which(argv[0]):
        return None
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout if out.returncode == 0 else None


# ---------------------------------------------------------------- windows

def focus_window(pids):
    """Raise a window owned by the first of ``pids`` that has one. True if raised, False if
    none of them has a window, None if this desktop gives us no way to tell."""
    pids = [p for p in pids if p != os.getpid()]  # a terminal atree launched has atree as a parent
    # GNOME Shell lets no client raise another's window on Wayland; our extension can
    out = _run(["gdbus", "call", "--session", "--timeout", "2", "--dest", "org.gnome.Shell",
                "--object-path", DBUS_PATH, "--method", "org.atree.Desktop.Activate",
                "@au [%s]" % ", ".join(map(str, pids))])
    if out is not None:
        return "true" in out
    if os.environ.get("XDG_SESSION_TYPE") == "wayland" or not os.environ.get("DISPLAY"):
        return None  # X11 tools would only see XWayland windows: their "no window" means nothing
    out = _run(["wmctrl", "-lp"])
    if out is not None:
        owned = {}
        for line in out.splitlines():
            f = line.split(None, 4)
            if len(f) >= 3 and f[2].isdigit():
                owned.setdefault(int(f[2]), f[0])
        win = next((owned[p] for p in pids if p in owned), None)
        return win is not None and _run(["wmctrl", "-i", "-a", win]) is not None
    if shutil.which("xdotool"):
        for p in pids:
            wins = (_run(["xdotool", "search", "--onlyvisible", "--pid", str(p)]) or "").split()
            if wins:
                return _run(["xdotool", "windowactivate", wins[-1]]) is not None
        return False
    return None


# ---------------------------------------------------------------- multiplexers

def _tmux_socket(server, env):
    """The socket of the tmux server ``server``: from $TMUX if the agent inherited it, else
    by matching the server's open sockets against /proc/net/unix."""
    sock = env.get("TMUX", "").split(",")[0]
    if sock:
        return sock
    fd_dir = os.path.join(base.PROC, str(server), "fd")
    inodes = set()
    try:
        for fd in os.listdir(fd_dir):
            link = os.readlink(os.path.join(fd_dir, fd))
            if link.startswith("socket:["):
                inodes.add(link[8:-1])
        with open(os.path.join(base.PROC, "net", "unix")) as f:
            for line in f:
                cols = line.split()
                if len(cols) >= 8 and cols[6] in inodes:
                    return cols[7]
    except OSError:
        pass
    return None


def _tmux(pid, pane_pid, server, title, run):
    sock = _tmux_socket(server, _environ(pid))
    tmux = ["tmux", "-S", sock] if sock else ["tmux"]
    out = _run(tmux + ["list-panes", "-a", "-F", "#{pane_pid} #{pane_id} #{session_id} #{session_attached}"])
    pane = next((f for f in (l.split() for l in (out or "").splitlines())
                 if len(f) == 4 and f[0] == str(pane_pid)), None)
    if pane is None:
        raise OpError("'%s' is running in tmux (pid %d), but atree can't reach that tmux server" % (title, pid))
    _, pane_id, session, attached = pane
    # the closest thing to a tab: show the agent's window and pane in the session
    _run(tmux + ["select-window", "-t", pane_id])
    _run(tmux + ["select-pane", "-t", pane_id])
    if attached != "0":
        clients = (_run(tmux + ["list-clients", "-t", session, "-F", "#{client_pid}"]) or "").split()
        for c in clients:
            if c.isdigit() and focus_window([int(c)] + [p for p, _ in ancestors(int(c))]):
                return "raised its tmux window"
    # detached, or its clients have no window we can raise (ssh, a console): tmux takes any
    # number of clients, so one more in a new terminal is safe
    run(shlex.join(tmux + ["attach-session", "-t", session]))
    return "attaching to its tmux session…"


def _screen(pid, server, run):
    # screen doesn't tell which process is an attached client, so its window can't be
    # found; -x attaches a detached session and shares one that is attached elsewhere
    sty = _environ(pid).get("STY") or str(server)
    run(shlex.join(["screen", "-x", sty]))
    return "attaching to its screen session…"


def reattach(pid, title, run):
    """Show the running process ``pid`` of talk ``title``; ``run(cmd)`` opens a terminal.
    Returns a status line, or None if the process is gone (resume it as usual)."""
    chain = ancestors(pid)
    if not chain and not base.pid_alive(pid):
        return None
    below = pid
    for p, comm in chain:
        if comm.startswith("tmux"):
            return _tmux(pid, below, p, title, run)
        if comm.lower() == "screen":
            return _screen(pid, p, run)
        below = p
    found = focus_window([pid] + [p for p, _ in chain])
    if found:
        return "raised its terminal window"
    if found is None:
        raise OpError("'%s' is already running (pid %d); atree can't raise windows on this desktop "
                      "(on GNOME, run atree.py --install-extension and log in again)" % (title, pid))
    # no window and no multiplexer: nothing (zellij, dtach, nohup, ssh) we know how to attach
    # to, and a second resume would write to the same transcript
    raise OpError("'%s' is already running (pid %d) without a terminal window atree can raise" % (title, pid))
