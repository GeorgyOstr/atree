"""iCalendar (RFC 5545 subset) parsing and recurrence expansion."""
import calendar as _calendar
import os
import re
import time as _time
import zoneinfo
from datetime import date, datetime, time, timedelta, timezone

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
LOCALTIME = "/etc/localtime"

WINDOWS_ZONES = {
    "UTC": "UTC",
    "GMT Standard Time": "Europe/London",
    "W. Europe Standard Time": "Europe/Berlin",
    "Romance Standard Time": "Europe/Paris",
    "Central Europe Standard Time": "Europe/Budapest",
    "Central European Standard Time": "Europe/Warsaw",
    "E. Europe Standard Time": "Europe/Chisinau",
    "FLE Standard Time": "Europe/Kiev",
    "GTB Standard Time": "Europe/Bucharest",
    "Russian Standard Time": "Europe/Moscow",
    "Eastern Standard Time": "America/New_York",
    "Central Standard Time": "America/Chicago",
    "Mountain Standard Time": "America/Denver",
    "Pacific Standard Time": "America/Los_Angeles",
    "Tokyo Standard Time": "Asia/Tokyo",
    "China Standard Time": "Asia/Shanghai",
    "India Standard Time": "Asia/Kolkata",
    "Singapore Standard Time": "Asia/Singapore",
    "AUS Eastern Standard Time": "Australia/Sydney",
}

WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
FREQS = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
RULE_PARTS = {"FREQ", "INTERVAL", "COUNT", "UNTIL", "WKST", "BYDAY", "BYMONTHDAY", "BYMONTH", "BYSETPOS"}
ERRORS = (ValueError, TypeError, OverflowError, ArithmeticError)


# ---------------------------------------------------------------- zones

def _iana(name):
    try:
        return zoneinfo.ZoneInfo(name)
    except Exception:
        return None


def local_zone():
    """The process's local zone, following ``TZ`` like the C library (spec R-tz-01)."""
    tz = os.environ.get("TZ")
    if tz == "":
        return UTC
    path = LOCALTIME
    if tz is not None:
        tz = tz[1:] if tz.startswith(":") else tz
        zone = _iana(tz) if tz else None
        if zone is not None:
            return zone
        path = tz if tz.startswith("/") else None
    if path:
        try:
            with open(path, "rb") as f:
                return zoneinfo.ZoneInfo.from_file(f)
        except Exception:
            pass
    return timezone(datetime.now().astimezone().utcoffset())


def resolve_tzid(tzid, local):
    name = tzid.strip('"')
    if name:
        for candidate in (name, WINDOWS_ZONES.get(name), "/".join(name.split("/")[-2:])):
            zone = _iana(candidate) if candidate else None
            if zone is not None:
                return zone
    return local


# ---------------------------------------------------------------- content lines

def unfold(text):
    return re.sub(r"\n[ \t]", "", text.replace("\r\n", "\n")).split("\n")


def split_line(line):
    """``(NAME, {PARAM: value}, value)`` or None when there is no unquoted colon."""
    parts, start, quoted = [], 0, False
    for i, c in enumerate(line):
        if c == '"':
            quoted = not quoted
        elif quoted:
            continue
        elif c == ";":
            parts.append(line[start:i])
            start = i + 1
        elif c == ":":
            parts.append(line[start:i])
            break
    else:
        return None
    params = {}
    for p in parts[1:]:
        key, _, val = p.partition("=")
        if len(val) >= 2 and val[0] == val[-1] == '"':
            val = val[1:-1]
        params[key.upper()] = val
    return parts[0].upper(), params, line[i + 1:]


def unescape(value):
    value = re.sub(r"\\(.)", lambda m: "\n" if m.group(1) in "nN" else m.group(1), value, flags=re.S)
    return " ".join(value.split())


def parse_value(value, params, local):
    """A date (all-day) or an aware datetime."""
    v = value.strip()
    if params.get("VALUE", "").upper() == "DATE" or re.fullmatch(r"[0-9]{8}", v):
        if not re.fullmatch(r"[0-9]{8}", v):
            raise ValueError("bad date")
        return date(int(v[:4]), int(v[4:6]), int(v[6:8]))
    if not re.fullmatch(r"[0-9]{8}T[0-9]{6}Z?", v):
        raise ValueError("bad date-time")
    if v.endswith("Z"):
        zone = UTC
    elif "TZID" in params:
        zone = resolve_tzid(params["TZID"], local)
    else:
        zone = local
    return datetime(int(v[:4]), int(v[4:6]), int(v[6:8]), int(v[9:11]), int(v[11:13]), int(v[13:15]),
                    tzinfo=zone)


def parse_duration(value):
    """``(nominal days, exact timedelta)``; zero when it doesn't match."""
    m = re.fullmatch(r"([+-])?P(?:([0-9]+)W)?(?:([0-9]+)D)?(?:T(?:([0-9]+)H)?(?:([0-9]+)M)?(?:([0-9]+)S)?)?",
                     value.strip().upper())
    if not m:
        return 0, timedelta(0)
    sign, w, d, h, mi, s = m.groups()
    nominal = 7 * int(w or 0) + int(d or 0)
    exact = timedelta(hours=int(h or 0), minutes=int(mi or 0), seconds=int(s or 0))
    return (-nominal, -exact) if sign == "-" else (nominal, exact)


# ---------------------------------------------------------------- events

class Event:
    __slots__ = ("uid", "title", "start", "end", "duration", "rrule", "exdates", "recurrence_id",
                 "cancelled")

    def __init__(self):
        self.uid = ""
        self.title = ""
        self.start = self.end = self.duration = self.rrule = self.recurrence_id = None
        self.exdates = []
        self.cancelled = False


def is_allday(v):
    return not isinstance(v, datetime)


def add(start, nominal, exact):
    """Add calendar days on the wall clock, then elapsed time (spec R-ical-11)."""
    if is_allday(start):
        return start + timedelta(days=nominal + int(exact / timedelta(days=1)))
    wall = start if nominal == 0 else (start + timedelta(days=nominal)).replace(fold=0)
    return (wall.astimezone(UTC) + exact).astimezone(start.tzinfo)


def parse(text, local=None):
    local = local or local_zone()
    events = []
    ev, raw, depth, broken = None, {}, 0, False
    for line in unfold(text):
        parsed = split_line(line)
        if parsed is None:
            continue
        name, params, value = parsed
        if ev is None:
            if name == "BEGIN" and value.strip().upper() == "VEVENT":
                ev, raw, depth, broken = Event(), {}, 0, False
            continue
        if name == "BEGIN":
            depth += 1
        elif name == "END":
            if depth:
                depth -= 1
            elif value.strip().upper() == "VEVENT":
                if not broken:
                    done = _finish(ev, raw)
                    if done is not None:
                        events.append(done)
                ev = None
        elif not depth and not broken:
            try:
                _property(ev, raw, name, params, value, local)
            except ValueError:
                pass
            except ERRORS:
                broken = True
    return events


def _property(ev, raw, name, params, value, local):
    if name == "UID":
        ev.uid = value
    elif name == "SUMMARY":
        ev.title = unescape(value)
    elif name in ("DTSTART", "DTEND", "RECURRENCE-ID"):
        raw[name] = parse_value(value, params, local)
    elif name == "DURATION":
        raw[name] = parse_duration(value)
    elif name == "STATUS":
        ev.cancelled = value.strip().upper() == "CANCELLED"
    elif name == "RRULE":
        rule = {}
        for part in value.upper().split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                rule[k] = v
        ev.rrule = rule
    elif name == "EXDATE":
        # all or nothing: a malformed item drops the whole property (R-ical-12)
        ev.exdates.extend([parse_value(item, params, local) for item in value.split(",") if item.strip()])


def _finish(ev, raw):
    start = raw.get("DTSTART")
    if start is None:
        return None
    ev.start = start
    ev.recurrence_id = raw.get("RECURRENCE-ID")
    try:
        if "DTEND" in raw:
            ev.end = raw["DTEND"]
        elif "DURATION" in raw:
            ev.duration = raw["DURATION"]
            ev.end = add(start, *ev.duration)
        else:
            ev.end = add(start, 1, timedelta(0)) if is_allday(start) else start
    except ERRORS:
        return None
    return ev


def calname(text):
    for line in unfold(text[:20000]):
        parsed = split_line(line)
        if parsed is None:
            continue
        name, _, value = parsed
        if name == "BEGIN" and value.strip().upper() == "VEVENT":
            break
        if name == "X-WR-CALNAME":
            return unescape(value)
    return ""


# ---------------------------------------------------------------- rules

def _ints(value, lo, hi, signed=True):
    out = []
    for item in value.split(","):
        if not re.fullmatch(r"[+-]?[0-9]+", item):
            raise ValueError("bad integer list")
        n = int(item)
        if not lo <= (abs(n) if signed else n) <= hi:
            raise ValueError("out of range")
        out.append(n)
    return out


def _positive(value):
    if not re.fullmatch(r"\+?[0-9]+", value) or int(value) < 1:
        raise ValueError("not a positive integer")
    return int(value)


class Rule:
    def __init__(self, parts, local):
        self.freq = parts.get("FREQ")
        self.simple = self.freq not in FREQS or not set(parts) <= RULE_PARTS
        self.count = _positive(parts["COUNT"]) if "COUNT" in parts else None
        self.until = parse_value(parts["UNTIL"], {}, local) if "UNTIL" in parts else None
        # an unsupported rule yields DTSTART alone, so its other parts are never checked
        check = parts if not self.simple else {}
        self.interval = _positive(check["INTERVAL"]) if "INTERVAL" in check else 1
        self.bymonth = sorted(set(_ints(check["BYMONTH"], 1, 12, False))) if "BYMONTH" in check else None
        self.bymonthday = _ints(check["BYMONTHDAY"], 1, 31) if "BYMONTHDAY" in check else None
        self.bysetpos = _ints(check["BYSETPOS"], 1, 366) if "BYSETPOS" in check else None
        wkst = parts.get("WKST")
        self.wkst = WEEKDAYS.index(wkst) if wkst in WEEKDAYS else 0
        self.byday = None
        if "BYDAY" in parts:
            byday = []
            for item in parts["BYDAY"].split(","):
                m = re.fullmatch(r"([+-]?[0-9]{1,3})?(MO|TU|WE|TH|FR|SA|SU)", item)
                if m and (m.group(1) is None or int(m.group(1)) != 0):
                    byday.append((int(m.group(1)) if m.group(1) else 0, WEEKDAYS.index(m.group(2))))
            self.byday = byday or None


def _nth(days, n):
    """Every day of ``days`` (n == 0) or just the n-th, counted from the end when negative."""
    if n == 0:
        return days
    if n > 0:
        return days[n - 1:n]
    return days[n:len(days) + n + 1] if -n <= len(days) else []


def _weekdays(first, last, wd):
    d = first + timedelta(days=(wd - first.weekday()) % 7)
    out = []
    while d <= last:
        out.append(d)
        d += timedelta(days=7)
    return out


def _monthday_ok(rule, d):
    if rule.bymonthday is None:
        return True
    n = _calendar.monthrange(d.year, d.month)[1]
    return any(d.day == (k if k > 0 else n + 1 + k) for k in rule.bymonthday)


def _month_days(rule, start, y, m):
    n = _calendar.monthrange(y, m)[1]
    days = None
    if rule.bymonthday is not None:
        days = {k if k > 0 else n + 1 + k for k in rule.bymonthday}
        days = {date(y, m, k) for k in days if 1 <= k <= n}
    if rule.byday is not None:
        first, last = date(y, m, 1), date(y, m, n)
        wd = {d for k, w in rule.byday for d in _nth(_weekdays(first, last, w), k)}
        days = wd if days is None else days & wd
    if days is None:
        days = {date(y, m, start.day)} if start.day <= n else set()
    return days


def _candidates(rule, start, freq_period):
    kind, value = freq_period
    if kind == "DAILY":
        d = value
        ok = (rule.byday is None or d.weekday() in {w for _, w in rule.byday}) and _monthday_ok(rule, d)
        days = {d} if ok else set()
    elif kind == "WEEKLY":
        wds = {w for _, w in rule.byday} if rule.byday else {start.weekday()}
        days = {value + timedelta(days=(w - rule.wkst) % 7) for w in wds}
    elif kind == "MONTHLY":
        y, m = value
        days = set() if rule.bymonth and m not in rule.bymonth else _month_days(rule, start, y, m)
    elif rule.byday is not None and rule.bymonth is None:
        y = value
        first, last = date(y, 1, 1), date(y, 12, 31)
        days = {d for k, w in rule.byday for d in _nth(_weekdays(first, last, w), k)}
        days = {d for d in days if _monthday_ok(rule, d)}
    else:
        y = value
        months = rule.bymonth or (range(1, 13) if rule.bymonthday is not None else [start.month])
        days = set().union(*(_month_days(rule, start, y, m) for m in months))
    if rule.bymonth:
        days = {d for d in days if d.month in rule.bymonth}
    days = sorted(days)
    if rule.bysetpos:
        days = sorted({days[p - 1 if p > 0 else p] for p in rule.bysetpos if -len(days) <= p <= len(days) and p})
    return days


def _span(lo, hi, interval):
    """Indices k >= 0 with lo <= k*interval <= hi."""
    return range(max(0, -(-lo // interval)), hi // interval + 1)


def _periods(rule, start, first, last):
    iv = rule.interval
    if rule.freq == "DAILY":
        for k in _span((first - start).days, (last - start).days, iv):
            yield "DAILY", start + timedelta(days=k * iv)
    elif rule.freq == "WEEKLY":
        week0 = start - timedelta(days=(start.weekday() - rule.wkst) % 7)
        for k in _span((first - week0).days - 6, (last - week0).days, 7 * iv):
            yield "WEEKLY", week0 + timedelta(days=7 * iv * k)
    elif rule.freq == "MONTHLY":
        m0 = start.year * 12 + start.month - 1
        for k in _span(first.year * 12 + first.month - 1 - m0, last.year * 12 + last.month - 1 - m0, iv):
            y, m = divmod(m0 + k * iv, 12)
            yield "MONTHLY", (y, m + 1)
    else:
        for k in _span(first.year - start.year, last.year - start.year, iv):
            yield "YEARLY", start.year + k * iv


def dates(start, rule, first, last):
    """The rule's dates within [max(first, start), last], in order (spec 7.4)."""
    first = max(first, start)
    if first > last:
        return
    if rule.simple:
        if first == start:
            yield start
        return
    for period in _periods(rule, start, first, last):
        for d in _candidates(rule, start, period):
            if first <= d <= last:
                yield d


# ---------------------------------------------------------------- expansion

def _instant(v, zone):
    """Position on the absolute time line; dates count as midnight in ``zone``."""
    if is_allday(v):
        v = datetime.combine(v, time(), zone)
    return v - EPOCH


def _same(a, b):
    if is_allday(a) or is_allday(b):
        return (a if is_allday(a) else a.date()) == (b if is_allday(b) else b.date())
    return a - EPOCH == b - EPOCH


def _exists(dt):
    back = dt.astimezone(UTC).astimezone(dt.tzinfo)
    return back.replace(tzinfo=None) == dt.replace(tzinfo=None)


def _valid_span(ev, zone):
    if is_allday(ev.start) != is_allday(ev.end):
        return False
    return _instant(ev.end, zone) >= _instant(ev.start, zone)


def _overlaps(s, e, lo, hi):
    zone = lo.tzinfo
    si, ei = _instant(s, zone), _instant(e, zone)
    return si < hi - EPOCH and (ei > lo - EPOCH or (ei == si and si >= lo - EPOCH))


def _instance(ev, s, e):
    return {"uid": ev.uid, "title": ev.title or "(no title)", "start": s, "end": e, "allday": is_allday(s)}


def _excluded(s, exclusions):
    return any(_same(s, x) for x in exclusions)


def _in_zone(dt, zone):
    return dt.astimezone(zone)


def _expand_master(ev, overrides, lo, hi, local):
    if not _valid_span(ev, lo.tzinfo):
        return []
    if ev.rrule is None:
        return [_instance(ev, ev.start, ev.end)] if _overlaps(ev.start, ev.end, lo, hi) else []
    rule = Rule(ev.rrule, local)
    allday = is_allday(ev.start)
    if ev.duration is not None:
        nominal, exact = ev.duration
    elif allday:
        nominal, exact = (ev.end - ev.start).days, timedelta(0)
    else:
        nominal, exact = 0, (ev.end - EPOCH) - (ev.start - EPOCH)
    zone = lo.tzinfo if allday else ev.start.tzinfo
    start_date = ev.start if allday else ev.start.date()

    until = rule.until
    if until is not None:
        if allday:
            until = until if is_allday(until) else until.date()
        elif is_allday(until):
            until = datetime.combine(until, time(23, 59, 59, 999999), zone)
    try:
        last = _in_zone(hi.astimezone(UTC) - timedelta(microseconds=1), zone).date()
    except OverflowError:
        last = date.max
    if until is not None:
        last = min(last, until if allday else _in_zone(until, zone).date())

    try:
        seek = _in_zone(lo.astimezone(UTC) - exact, zone).date() - timedelta(days=nominal)
        if not allday and nominal:
            seek -= timedelta(days=1)
        visible = max(start_date, seek)
    except ERRORS:
        visible = start_date

    exclusions = list(ev.exdates) + [o.recurrence_id for o in overrides]
    hi_abs = hi - EPOCH
    out, n = [], 0
    for d in dates(start_date, rule, start_date if rule.count else visible, last):
        if allday:
            s = d
        elif d == start_date:
            s = ev.start
        else:
            s = datetime.combine(d, ev.start.time().replace(fold=0), zone)
            if not _exists(s):
                continue
        n += 1
        if rule.count is not None and n > rule.count:
            break
        if until is not None and (d > until if allday else s - EPOCH > until - EPOCH):
            break
        if _instant(s, lo.tzinfo) >= hi_abs:
            break
        if d < visible or _excluded(s, exclusions):
            continue
        e = add(s, nominal, exact)
        if _overlaps(s, e, lo, hi):
            out.append(_instance(ev, s, e))
    return out


def expand(events, lo, hi, local=None):
    """Instances overlapping [lo, hi), sorted (spec 7)."""
    local = local or local_zone()
    overrides = {}
    for ev in events:
        if ev.recurrence_id is not None:
            overrides.setdefault(ev.uid, []).append(ev)
    out = []
    for ev in events:
        if ev.recurrence_id is not None:
            if ev.cancelled:
                continue
            try:
                if _valid_span(ev, lo.tzinfo) and _overlaps(ev.start, ev.end, lo, hi):
                    out.append(_instance(ev, ev.start, ev.end))
            except ERRORS:
                pass
        elif not ev.cancelled:
            try:
                out.extend(_expand_master(ev, overrides.get(ev.uid, ()), lo, hi, local))
            except ERRORS:
                pass
    zone = lo.tzinfo
    out.sort(key=lambda i: (_instant(i["start"], zone), not i["allday"], i["title"]))
    return out


def instances(text, days=7, now=None):
    """Instances from today's local midnight for ``days`` days, JSON-ready (spec 7.6)."""
    zone = local_zone()
    today = datetime.fromtimestamp(_time.time() if now is None else now, zone).date()
    lo = datetime.combine(today, time(), zone)
    hi = datetime.combine(today + timedelta(days=days), time(), zone)
    out = []
    for i in expand(parse(text, zone), lo, hi, zone):
        if i["allday"]:
            i["start"], i["end"] = i["start"].isoformat(), i["end"].isoformat()
        else:
            i["start"], i["end"] = i["start"].timestamp(), i["end"].timestamp()
        out.append(i)
    return out
