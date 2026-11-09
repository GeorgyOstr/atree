"""Linked iCal calendars: the private list, the fetched-instances cache, and fetching.

Calendar URLs are secrets: they never reach messages, errors, logs or the cache.
"""
import http.client
import json
import os
import re
import urllib.error
import urllib.request
from datetime import date

from . import timers
from .fsutil import atomic_write

CACHE_DIR = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache"),
                         "atree")
LIST = os.path.join(timers.DATA, "calendars.json")
CACHE = os.path.join(CACHE_DIR, "calendar.json")

PALETTE = ("#4aa3ff", "#f2b84b", "#3ddc84", "#ff7ab6", "#b48cff", "#5fd7d7")
MAX_BYTES = 20_000_000
_URL = re.compile(r"[a-z][a-z0-9+.-]*://", re.I)


class FetchError(Exception):
    """A user-facing fetch failure; the message never contains the address."""


def _json(path):
    try:
        with open(path, "rb") as f:
            return json.loads(f.read())
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- list

def _valid_calendar(c):
    return (isinstance(c, dict)
            and isinstance(c.get("id"), str) and c["id"] != ""
            and isinstance(c.get("url"), str) and c["url"].strip() != ""
            and isinstance(c.get("name", ""), str)
            and isinstance(c.get("color", ""), str))


def load_list():
    data = _json(LIST)
    return [c for c in data if _valid_calendar(c)] if isinstance(data, list) else []


def save_list(cals):
    atomic_write(LIST, json.dumps(cals, indent=1), 0o600)


def pick_color(cals):
    used = {c.get("color") for c in cals}
    for color in PALETTE:
        if color not in used:
            return color
    return PALETTE[len(cals) % len(PALETTE)]


# ---------------------------------------------------------------- cache

def _iso_date(v):
    if not isinstance(v, str):
        return False
    try:
        date.fromisoformat(v)
    except ValueError:
        return False
    return True


def _valid_event(e):
    if not (isinstance(e, dict) and isinstance(e.get("uid"), str) and isinstance(e.get("title"), str)
            and type(e.get("allday")) is bool):
        return False
    check = _iso_date if e["allday"] else timers.valid_ts
    return check(e.get("start")) and check(e.get("end"))


def load_cache():
    data = _json(CACHE)
    if not isinstance(data, dict):
        return {}
    out = {}
    for cid, rec in data.items():
        if not isinstance(rec, dict):
            continue
        events = rec.get("events", [])
        if not isinstance(events, list) or not timers.valid_ts(rec.get("at", 0)):
            continue
        out[cid] = dict(rec, events=[e for e in events if _valid_event(e)])
    return out


def save_cache(cache):
    atomic_write(CACHE, json.dumps(cache, separators=(",", ":")), 0o600)


# ---------------------------------------------------------------- fetch

def _unreachable(reason):
    if isinstance(reason, OSError):
        desc = reason.strerror
        if isinstance(desc, str) and desc and not _URL.search(desc):
            return "can't reach the server: " + desc
        if isinstance(reason, TimeoutError) and not desc:
            return "can't reach the server: timed out"
    return "can't reach the server"


def fetch(url):
    """The calendar text at ``url`` (http, https or webcal); raises FetchError."""
    url = url.strip()
    if url[:9].lower() == "webcal://":
        url = "https://" + url[9:]
    if not url.lower().startswith(("http://", "https://")):
        raise FetchError("not an http(s) or webcal address")
    error = None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "atree"})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = response.read(MAX_BYTES)
    except urllib.error.HTTPError as e:
        e.close()
        error = "the server answered HTTP %d" % e.code
    except urllib.error.URLError as e:
        error = _unreachable(e.reason)
    except (OSError, ValueError, http.client.HTTPException) as e:
        error = "download failed (%s)" % type(e).__name__
    if error is not None:
        raise FetchError(error)  # outside the handler: no chained exception carrying the URL
    text = data.decode("utf-8", "replace")
    if "begin:vcalendar" not in text[:2000].lower():
        raise FetchError("that address doesn't serve an iCal calendar")
    return text
