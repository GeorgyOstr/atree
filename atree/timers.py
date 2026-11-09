"""Countdown timers and alarms: shared storage, cross-process claiming, text parsing."""
import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from datetime import time as dtime

from .fsutil import atomic_write

DATA = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share"),
                    "atree")
FILE = os.path.join(DATA, "timers.json")
LOCK = os.path.join(DATA, "timers.lock")

CLAIM_TTL = 172800
SOUND = "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"


def valid_ts(v):
    """A finite int/float (not bool) that converts to a local date-time."""
    if type(v) not in (int, float):
        return False
    try:
        datetime.fromtimestamp(v)
    except (OverflowError, OSError, ValueError):
        return False
    return True


def new_id():
    return format(time.time_ns(), "x")


# ---------------------------------------------------------------- storage

def _valid_timer(t):
    return (isinstance(t, dict)
            and isinstance(t.get("id"), str) and t["id"] != ""
            and valid_ts(t.get("due"))
            and isinstance(t.get("label", ""), str)
            and all(type(t.get(k, False)) is bool for k in ("alarm", "fired")))


def _read():
    try:
        with open(FILE, "rb") as f:
            data = json.loads(f.read().decode("utf-8"))
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        data = {}
    timers = data.get("timers")
    claimed = data.get("claimed")
    timers = [t for t in timers if _valid_timer(t)] if isinstance(timers, list) else []
    claimed = {k: v for k, v in claimed.items() if valid_ts(v)} if isinstance(claimed, dict) else {}
    return timers, claimed


def _write(timers, claimed):
    atomic_write(FILE, json.dumps({"timers": timers, "claimed": claimed}, indent=1))


def load():
    return _read()[0]


@contextlib.contextmanager
def lock():
    """Exclusive advisory lock shared by every atree process (timers and calendars.json)."""
    os.makedirs(DATA, exist_ok=True)
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def edit(fn):
    with lock():
        timers, claimed = _read()
        result = fn(timers)
        if result is not None:
            timers = result
        _write(timers, claimed)
        return timers


def claim_due(now):
    """Mark due timers fired; returns (timers this call marked, all timers)."""
    with lock():
        timers, claimed = _read()
        mine = []
        for t in timers:
            if not t.get("fired") and t["due"] <= now:
                t["fired"] = True
                mine.append(t)
        if mine:
            _write(timers, claimed)
        return mine, timers


def claim_once(key):
    """True for exactly one caller across processes."""
    with lock():
        timers, claimed = _read()
        if key in claimed:
            return False
        now = time.time()
        claimed = {k: v for k, v in claimed.items() if v >= now - CLAIM_TTL}
        claimed[key] = now
        _write(timers, claimed)
        return True


# ---------------------------------------------------------------- notification

def _spawn(argv):
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception:
        return

    def reap():
        try:
            p.wait(30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
        except Exception:
            pass
    threading.Thread(target=reap, daemon=True).start()


def notify(title, body, sound=True):
    """Desktop notification plus an alarm sound; never blocks, never raises."""
    try:
        if shutil.which("notify-send"):
            _spawn(["notify-send", "-a", "atree", "-i", "alarm-symbolic", title, body])
        if not sound:
            return
        if shutil.which("canberra-gtk-play"):
            _spawn(["canberra-gtk-play", "-i", "alarm-clock-elapsed", "-d", "atree"])
        elif shutil.which("paplay") and os.path.exists(SOUND):
            _spawn(["paplay", SOUND])
    except Exception:
        pass


# ---------------------------------------------------------------- parsing

_CLOCK = re.compile(r"(\d{1,2})[:.](\d{2})")
_UNITS = re.compile(r"(?:(\d+)h)?(?:(\d+)(?:min|m))?(?:(\d+)(?:sec|s))?")
_H_MIN = re.compile(r"(\d+)h(\d+)")
_BARE = re.compile(r"\d+")


class _Invalid(Exception):
    pass


def _alarm(h, m, now):
    today = datetime.fromtimestamp(now).date()
    for day in (today, today + timedelta(days=1)):
        wall = datetime.combine(day, dtime(h, m))
        real = []
        for fold in (0, 1):
            t = wall.replace(fold=fold).timestamp()
            if datetime.fromtimestamp(t).replace(fold=0) == wall:
                real.append(t)
        future = [t for t in (real or [wall.timestamp()]) if t > now]
        if future:
            return min(future)
    return None


def _seconds(spec):
    m = _UNITS.fullmatch(spec)
    if m:
        h, mi, s = (int(x or 0) for x in m.groups())
        return h * 3600 + mi * 60 + s
    m = _H_MIN.fullmatch(spec)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60
    if _BARE.fullmatch(spec):
        return int(spec) * 60
    return 0


def _match(spec, now):
    m = _CLOCK.fullmatch(spec)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if h > 23 or mi > 59:
            raise _Invalid
        try:
            due = _alarm(h, mi, now)
        except (OverflowError, OSError, ValueError):
            raise _Invalid from None
        return None if due is None else (due, True)
    try:
        secs = _seconds(spec)
        if not secs:
            return None
        due = now + secs
        datetime.fromtimestamp(due)
    except (OverflowError, OSError, ValueError):
        raise _Invalid from None
    return due, False


def parse(text, now):
    """``{"due", "alarm", "label"}`` for timer input, or None (spec 4)."""
    words = text.split()
    for n in (3, 2, 1):
        if len(words) < n:
            continue
        if n > 1 and words[0].isdigit() and words[1][:1].isdigit():
            continue
        try:
            hit = _match("".join(words[:n]).lower(), now)
        except _Invalid:
            return None
        if hit:
            return {"due": hit[0], "alarm": hit[1], "label": " ".join(words[n:])}
    return None


def hhmm(epoch):
    return time.strftime("%H:%M", time.localtime(epoch))
