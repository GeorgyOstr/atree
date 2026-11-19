"""Plan usage of every Claude Code account here (~/.claude, ~/.claude1, ...).

Each account's limits and extra-usage credits come from the claude.ai usage endpoint, called
with the access token Claude Code saved for that account. atree never refreshes a token (that
would rotate it under Claude Code); once it has expired, the account's status-line data is used.
"""
import glob
import hashlib
import json
import os
import threading
import time
import urllib.request

from . import statusline
from .agents import base

ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
TTL = 180  # seconds between endpoint calls per account
BACKOFF = 600  # after a failed call (offline, rate limited) wait this long before the next
WINDOWS = (("five_hour", "session (5h)", 18000), ("seven_day", "week", 604800))

_lock = threading.Lock()
_cache = {}  # account dir -> (checked at, result, fetched ok)


def name_of(d):
    return os.path.basename(os.path.normpath(d)).lstrip(".") or d


def account_dirs(default_dir):
    """``default_dir`` plus its sibling .claude* dirs (~/.claude1, ...) holding credentials or
    status-line data."""
    dirs = [default_dir]
    seen = {os.path.realpath(default_dir)}
    parent = os.path.dirname(os.path.normpath(default_dir))
    for d in glob.glob(os.path.join(glob.escape(parent), ".claude*")):
        real = os.path.realpath(d)
        if real in seen or not os.path.isdir(d):
            continue
        if os.path.exists(os.path.join(d, ".credentials.json")) or os.path.exists(statusline.limits_path(d)):
            dirs.append(d)
            seen.add(real)
    return sorted(dirs, key=name_of)


def _token(d, now):
    try:
        with open(os.path.join(d, ".credentials.json"), "rb") as f:
            c = json.loads(f.read())["claudeAiOauth"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    exp = c.get("expiresAt")
    token = c.get("accessToken")
    if type(exp) not in (int, float) or exp / 1000 < now + 60 or not isinstance(token, str):
        return None
    return token


def _identity(d):
    """Who an account's credentials belong to (a copy of a config dir is the same account)."""
    try:
        with open(os.path.join(d, ".credentials.json"), "rb") as f:
            c = json.loads(f.read())["claudeAiOauth"]
        return hashlib.sha256(str(c.get("refreshToken") or c.get("accessToken")).encode()).hexdigest()
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def _fetch(token):
    req = urllib.request.Request(ENDPOINT, headers={
        "Authorization": "Bearer " + token, "anthropic-beta": "oauth-2025-04-20", "User-Agent": "atree"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read())


def _bar(label, window, used, resets, now):
    if isinstance(resets, str):
        resets = base.iso_to_epoch(resets)
    elif type(resets) not in (int, float):
        resets = None
    stale = bool(resets) and resets <= now
    return {"label": label, "used": 0.0 if stale else float(used), "window": window,
            "resets_at": resets, "stale": stale}


def from_api(j, now):
    """Endpoint reply → (bars, credits); credits is None, {"off": reason} or used/limit."""
    bars = []
    for key, label, window in WINDOWS:
        b = j.get(key)
        if isinstance(b, dict) and type(b.get("utilization")) in (int, float):
            bars.append(_bar(label, window, b["utilization"], b.get("resets_at"), now))
    credits = None
    x = j.get("extra_usage")
    if isinstance(x, dict):
        dp = x.get("decimal_places")
        scale = 10 ** (dp if isinstance(dp, int) and 0 <= dp <= 4 else 2)
        limit = x.get("monthly_limit")
        if x.get("is_enabled") and type(limit) in (int, float) and limit > 0:
            used = x.get("used_credits")
            credits = {"used": float(used if type(used) in (int, float) else 0) / scale,
                       "limit": limit / scale, "currency": str(x.get("currency") or "USD")}
        elif x.get("is_enabled") or x.get("credits_ever_enabled"):
            credits = {"off": str(x.get("disabled_reason") or "off").replace("_", " ")}
    return bars, credits


def from_statusline(saved, now):
    limits = saved.get("rate_limits") or {}
    bars = []
    for key, label, window in WINDOWS:
        b = limits.get(key) if isinstance(limits, dict) else None
        if isinstance(b, dict) and type(b.get("used_percentage")) in (int, float):
            bars.append(_bar(label, window, b["used_percentage"], b.get("resets_at"), now))
    return bars


def account(d, now, fetch=None):
    """One account's usage, or None when nothing is known about it."""
    with _lock:
        hit = _cache.get(d)
    if hit and now - hit[0] < (TTL if hit[2] else BACKOFF):
        return hit[1]
    token = _token(d, now)
    if token:
        try:
            bars, credits = from_api((fetch or _fetch)(token), now)
            result = {"name": name_of(d), "bars": bars, "credits": credits, "observed_at": now}
            with _lock:
                _cache[d] = (now, result, True)
            return result
        except Exception:  # offline, rate limited, changed reply: the status line still knows
            pass
    # no fresh reply: whichever is newer, the status line or the last reply (credits only come
    # from replies)
    last = hit[1] if hit else None
    saved = statusline.limits(statusline.limits_path(d))
    result = last
    if saved and (last is None or (saved.get("at") or 0) > (last.get("observed_at") or 0)):
        result = {"name": name_of(d), "bars": from_statusline(saved, now),
                  "credits": last.get("credits") if last else None, "observed_at": saved.get("at")}
    if token and result is not None:  # the call failed: back off instead of retrying every poll
        with _lock:
            _cache[d] = (now, result, False)
    return result


def all_accounts(default_dir, now=None):
    now = time.time() if now is None else now
    out, owners = [], set()
    for d in account_dirs(default_dir):
        who = _identity(d)
        if who is not None and who in owners:  # e.g. cp -r ~/.claude ~/.claude-bak
            continue
        owners.add(who)
        a = account(d, now)
        if a is not None:
            out.append(a)
    return out
