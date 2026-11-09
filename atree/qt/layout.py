"""Window-free presentation logic: visibility, ordering, tree layout, panels' data.

Everything here is plain Python so it can be tested without Qt.
"""
import datetime as _dt
import math
import os
import time

from atree.agents.base import fmt_tokens

PILL_H = 28
CARD_W = 430
HEAD_H = 30
TRUNK_MIN = 3      # a folder with this many branches (and the root) grows them from a trunk
TRUNK_X = 14       # the trunk drops from under the pill's chevron
TRUNK_INDENT = 34  # branches start this far right of their folder's left edge
ROW_H = 26
CARD_PAD = 4
H_GAP = 38
V_GAP = 10
GRID_GAP = 12
COMPACT_ROWS = 5
FULL_ROWS = 14

DAY = 86400
LIVE_BONUS = 1e10
FOLD_AFTER = 14 * DAY
COMPACT_WITHIN = 10 * DAY
OLD_AFTER = 14 * DAY

SORTS = ("recent", "context", "tokens", "prompts", "name")
CARD_MODES = ("collapsed", "compact", "full")
QUIET_BRANCHES = ("", "HEAD", "main", "master")
PLACEHOLDER_TITLE = "No longer in view"

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def approx_width(text, px=13, bold=True):
    """Font-free text width estimate used when no real metrics are given."""
    return len(text) * px * (0.62 if bold else 0.56)


def collapse(text):
    return " ".join(str(text or "").split())


def tildify(path, home):
    if path == home:
        return "~"
    if home and path.startswith(home.rstrip("/") + "/"):
        return "~" + path[len(home.rstrip("/")):]
    return path


def identity():
    import getpass
    import socket
    try:
        user = getpass.getuser()
    except Exception:
        user = "?"
    return {"user": user, "host": socket.gethostname().split(".")[0]}


def age_text(seconds):
    s = max(0, seconds)
    if s < 60:
        return "now"
    if s < 3600:
        return "%dm" % (s // 60)
    if s < DAY:
        return "%dh" % (s // 3600)
    if s < 14 * DAY:
        return "%dd" % (s // DAY)
    if s < 60 * DAY:
        return "%dw" % (s // (7 * DAY))
    return "%dmo" % (s // (30 * DAY))


# ------------------------------------------------------------------ filters

def default_filters(agent_names):
    f = {name: True for name in agent_names}
    f.update(live=False, archived=False, age=30, sort="recent", reverse=False, q="")
    return f


def query_of(filters):
    return str(filters.get("q") or "").strip().lower()


def matches(talk, q):
    if not q:
        return False
    for field in ("title", "first_msg", "cwd", "id", "branch"):
        v = talk.get(field)
        if v and q in str(v).lower():
            return True
    return False


def _with_parents(chosen, pool):
    """``chosen`` keys plus their parent chains found in ``pool`` (key → talk)."""
    out = set()
    for key in chosen:
        while key in pool and key not in out:
            out.add(key)
            key = pool[key].get("parent")
    return out


def visible_talks(talks, filters, now, ai_keys=None):
    def enabled(t):
        return filters.get(t.get("tool"), True) is not False

    if ai_keys:
        pool = {t["key"]: t for t in talks if enabled(t)}
        keep = _with_parents([k for k in ai_keys if k in pool], pool)
        return [t for t in talks if t["key"] in keep and t["key"] in pool]
    age = filters.get("age") or 0
    base = []
    for t in talks:
        if not enabled(t):
            continue
        if t.get("archived") and not filters.get("archived"):
            continue
        if filters.get("live") and not t.get("live"):
            continue
        if age > 0 and not t.get("live") and now - (t.get("updated") or 0) > age * DAY:
            continue
        base.append(t)
    q = query_of(filters)
    if not q:
        return base
    pool = {t["key"]: t for t in base}
    keep = _with_parents([t["key"] for t in base if matches(t, q)], pool)
    return [t for t in base if t["key"] in keep]


# ------------------------------------------------------------------ sorting

def natural_recent(t):
    return -((t.get("updated") or 0) + (LIVE_BONUS if t.get("live") else 0))


def talk_key(t, sort, ranks=None):
    if sort == "name":
        return collapse(t.get("title")).lower()
    if sort == "recent":
        r = ranks.get(t["key"]) if ranks else None
        return natural_recent(t) if r is None else r
    return -(t.get(sort) or 0)


def aggregate(keys, sort):
    """Rank of a folder from its talks' keys (smaller first)."""
    if not keys:
        return 0
    if sort in ("tokens", "prompts"):
        return sum(keys)          # sum of negated values = −sum
    return min(keys)              # max score = min key


class Ranks:
    """Sticky Recent ranks (stable view, ui.md §6.2)."""

    PROMOTE_AFTER = 8.0

    def __init__(self):
        self.ranks = None
        self.since = {}       # key → monotonic time the current running streak began
        self.spent = set()    # keys whose streak may no longer promote

    def get(self, key, default=None):
        return default if self.ranks is None else self.ranks.get(key, default)

    def as_dict(self):
        return self.ranks or {}

    def reset(self, talks, mono):
        self.ranks = {t["key"]: natural_recent(t) for t in talks}
        self.since = {t["key"]: mono for t in talks if t.get("live")}
        self.spent = set(self.since)

    def update(self, talks, mono):
        if self.ranks is None:
            self.reset(talks, mono)
            return
        # one front per scan: every promotion in this scan shares it
        front = min(self.ranks.values()) - 1 if self.ranks else None
        for t in talks:
            key, live = t["key"], bool(t.get("live"))
            nat = natural_recent(t)
            if key not in self.ranks:
                if live:
                    self.ranks[key] = nat if front is None else min(nat, front)
                    self.since[key] = mono
                    self.spent.add(key)
                else:
                    self.ranks[key] = nat
                continue
            if not live:
                self.since.pop(key, None)
                self.spent.discard(key)
                continue
            start = self.since.setdefault(key, mono)
            if key not in self.spent and mono - start >= self.PROMOTE_AFTER:
                self.ranks[key] = min(nat, front)
                self.spent.add(key)
        present = {t["key"] for t in talks}
        self.ranks = {k: v for k, v in self.ranks.items() if k in present}
        self.since = {k: v for k, v in self.since.items() if k in present}
        self.spent &= present


# ------------------------------------------------------------------ layout

class Ctx:
    """Everything a layout depends on besides the talks."""

    def __init__(self, filters, now, home, dirs=None, cards=None, ranks=None,
                 selection=(), ai_keys=(), measure=None):
        self.filters = filters
        self.now = now
        self.home = home
        self.dirs = dirs or {}
        self.cards = cards or {}
        self.ranks = ranks or {}
        self.selection = set(selection)
        self.ai_keys = set(ai_keys)
        self.measure = measure or approx_width
        self.sort = filters.get("sort", "recent")
        self.reverse = bool(filters.get("reverse"))
        self.q = "" if self.ai_keys else query_of(filters)


def row_meta(t, sort, now):
    parts = []
    if t.get("live"):
        parts.append(t.get("live_status") or "running")
    parts.append(age_text(now - (t.get("updated") or 0)))
    if t.get("prompts"):
        parts.append("%dp" % t["prompts"])
    if sort == "context":
        parts.append("%s ctx" % fmt_tokens(t.get("context") or 0))
    if sort == "tokens":
        parts.append("%s tok" % fmt_tokens(t.get("tokens") or 0))
    n = t.get("agents") or 0
    if n:
        parts.append("%d agent%s" % (n, "" if n == 1 else "s"))
    if t.get("related"):
        parts.append("⑂")
    branch = t.get("branch") or ""
    if branch not in QUIET_BRANCHES:
        parts.append("⎇ " + branch)
    if t.get("archived"):
        parts.append("archived")
    return " · ".join(parts)


def cut(text, n):
    text = text or ""
    return text if len(text) <= n else text[:n] + "…"


def row_data(t, depth, ctx):
    key = t["key"]
    return {
        "uid": key, "key": key,
        "title": collapse(t.get("title")),
        "meta": row_meta(t, ctx.sort, ctx.now),
        "tool": t.get("tool", ""),
        "live": bool(t.get("live")),
        "busy": bool(t.get("live")) and t.get("live_status") == "busy",
        "archived": bool(t.get("archived")),
        "selected": key in ctx.selection,
        "match": key in ctx.ai_keys or matches(t, ctx.q),
        "old": ctx.now - (t.get("updated") or 0) > OLD_AFTER,
        "depth": depth,
        "tip": cut(t.get("first_msg") or "", 280),
    }


def placeholder_row(uid, depth):
    return {"uid": uid, "key": "", "title": PLACEHOLDER_TITLE, "meta": "", "tool": "",
            "live": False, "busy": False, "archived": False, "selected": False,
            "match": False, "old": False, "depth": depth, "tip": ""}


class _Dir:
    __slots__ = ("path", "subs", "talks")

    def __init__(self, path):
        self.path = path
        self.subs = {}
        self.talks = []


class _Node:
    __slots__ = ("kind", "path", "label", "name", "children", "items", "rank")

    def __init__(self, kind, path, label, children=(), items=(), name=""):
        self.kind = kind          # "dir" | "card" | "grid"
        self.path = path
        self.label = label
        self.name = name          # own directory name in the parent (first segment of a chain)
        self.children = list(children)
        self.items = list(items)  # (talk, depth) of every talk below, rows order for cards
        self.rank = 0


class Layout:
    """Emitted geometry: ``nodes`` (folders and cards), ``links``, ``rows`` per card path."""

    def __init__(self, nodes=(), links=(), rows=None, width=0.0, height=0.0, shape=None):
        self.nodes = list(nodes)
        self.links = list(links)
        self.rows = rows or {}
        self.width = width
        self.height = height
        self.shape = self._shape() if shape is None else shape

    def _shape(self):
        nodes = tuple((n["uid"], n["kind"], n["nx"], n["ny"], n["nw"], n["nh"],
                       n["mode"], n["collapsed"]) for n in self.nodes)
        links = tuple((l["uid"], l["x1"], l["y1"], l["x2"], l["y2"], l["trunk"]) for l in self.links)
        rows = tuple(sorted((p, tuple((r["uid"], r["depth"]) for r in rs))
                            for p, rs in self.rows.items()))
        return nodes, links, rows

    def items(self):
        return self.nodes + self.links


def _attach(talks):
    """Split talks into top-level ones and parent key → children (created, key order)."""
    by_key = {t["key"]: t for t in talks}
    kids = {}
    tops = []
    for t in talks:
        p = t.get("parent")
        if p and p in by_key and not _cycles(t, by_key):
            kids.setdefault(p, []).append(t)
        else:
            tops.append(t)
    for lst in kids.values():
        lst.sort(key=lambda c: (c.get("created") or 0, c["key"]))
    return tops, kids


def _cycles(t, by_key):
    seen = {t["key"]}
    p = t.get("parent")
    while p in by_key:
        if p in seen:
            return True
        seen.add(p)
        p = by_key[p].get("parent")
    return False


def _expand(t, kids, depth=0):
    yield t, depth
    for c in kids.get(t["key"], ()):
        yield from _expand(c, kids, depth + 1)


def _trie(tops):
    root = _Dir("/")
    for t in tops:
        node = root
        parts = [p for p in str(t.get("cwd") or "").split("/") if p]
        for i, name in enumerate(parts):
            sub = node.subs.get(name)
            if sub is None:
                sub = node.subs[name] = _Dir("/" + "/".join(parts[:i + 1]))
            node = sub
        node.talks.append(t)
    return root


def _build(d, label, is_root, kids, ctx):
    name = label  # the child's own directory name in its parent: the first segment of a chain
    while not d.talks and len(d.subs) == 1:
        (seg, sub), = d.subs.items()
        label = label + "/" + seg
        d, is_root = sub, False
    shown = tildify(label, ctx.home) if label else ("/" if is_root else "")
    rows_key = lambda t: (talk_key(t, ctx.sort, ctx.ranks), t["key"])
    own = sorted(d.talks, key=rows_key, reverse=ctx.reverse)
    own_items = [it for t in own for it in _expand(t, kids)]
    if not d.subs and not is_root:
        return _Node("card", d.path, shown, items=own_items, name=name)
    children = [_build(sub, sub_name, False, kids, ctx) for sub_name, sub in d.subs.items()]
    if d.talks:
        children.append(_Node("card", d.path, "", items=own_items))
    for c in children:
        if ctx.sort == "name":
            c.rank = c.name.lower()
        else:
            c.rank = aggregate([talk_key(t, ctx.sort, ctx.ranks) for t, _ in c.items], ctx.sort)
    children.sort(key=lambda c: (c.rank, c.path), reverse=ctx.reverse)
    packed, run = [], []
    for c in children + [None]:
        if c is not None and c.kind == "card":
            run.append(c)
            continue
        if len(run) > 1:
            packed.append(_Node("grid", run[0].path, "", children=run,
                                items=[it for r in run for it in r.items]))
        else:
            packed.extend(run)
        run = []
        if c is not None:
            packed.append(c)
    items = [it for c in packed for it in c.items]
    return _Node("dir", d.path, shown, children=packed, items=items, name=name)


def _stats(items, now):
    live = sum(1 for t, _ in items if t.get("live"))
    last = max((t.get("updated") or 0 for t, _ in items), default=0)
    return live, last


def card_mode(path, items, ctx):
    if ctx.q or ctx.ai_keys:
        return "full"
    m = ctx.cards.get(path)
    if m in CARD_MODES:
        return m
    live, last = _stats(items, ctx.now)
    return "compact" if live or ctx.now - last < COMPACT_WITHIN else "collapsed"


def dir_folded(path, items, has_children, ctx):
    if not has_children or ctx.ai_keys:
        return False
    o = ctx.dirs.get(path)
    if isinstance(o, bool):
        return o
    live, last = _stats(items, ctx.now)
    if ctx.q or live:
        return False
    return ctx.now - last > FOLD_AFTER


def card_height(mode, n):
    if mode == "collapsed":
        return HEAD_H
    if mode == "full":
        shown = min(n, FULL_ROWS)
    else:
        shown = n if n <= COMPACT_ROWS + 1 else COMPACT_ROWS
    return HEAD_H + shown * ROW_H + 2 * CARD_PAD


def card_width(mode, label, live, measure):
    if mode != "collapsed":
        return CARD_W
    return min(CARD_W, measure(label or "talks", 13, True) + 135 + (85 if live else 0))


def compute_layout(talks, ctx):
    """Layout of the visible ``talks`` (already filtered)."""
    tops, kids = _attach(talks)
    if not tops:
        return Layout()
    top = _build(_trie(tops), "", True, kids, ctx)
    nodes, links, rows = [], [], {}
    cursor = 0.0

    def emit_card(node, x, top_y):
        live, last = _stats(node.items, ctx.now)
        mode = card_mode(node.path, node.items, ctx)
        h = card_height(mode, len(node.items))
        w = card_width(mode, node.label, live, ctx.measure)
        rows[node.path] = [row_data(t, d, ctx) for t, d in node.items]
        nodes.append({
            "uid": "card:" + node.path, "kind": "card", "nx": x, "ny": top_y, "nw": w, "nh": h,
            "label": node.label, "path": node.path,
            "count": len(node.items), "talks": sum(1 for _, d in node.items if d == 0),
            "live": live, "collapsed": mode == "collapsed", "mode": mode,
            "age": age_text(ctx.now - last) if last else "",
            "_last": last, "_keys": [(t["key"], d) for t, d in node.items]})
        return h

    def place(node, x):
        """Lay ``node`` out at ``x``; returns (vertical center, live below)."""
        nonlocal cursor
        if node.kind == "card":
            top_y = cursor
            h = emit_card(node, x, top_y)
            cursor += h + V_GAP
            return top_y + HEAD_H / 2, nodes[-1]["live"]
        if node.kind == "grid":
            cols = 3 if len(node.children) >= 7 else 2
            col_y = [cursor] * cols
            first = None
            for i, card in enumerate(node.children):
                c = i % cols
                h = emit_card(card, x + c * (CARD_W + GRID_GAP), col_y[c])
                if first is None:
                    first = col_y[c] + HEAD_H / 2
                col_y[c] += h + GRID_GAP
            cursor = max(col_y) - GRID_GAP + V_GAP
            live, _ = _stats(node.items, ctx.now)
            return first, live
        live, last = _stats(node.items, ctx.now)
        has_children = bool(node.children)
        folded = dir_folded(node.path, node.items, has_children, ctx)
        w = ctx.measure(node.label, 13, True) + 96
        me = {"uid": "dir:" + node.path, "kind": "dir", "nx": x, "ny": 0.0, "nw": w,
              "nh": PILL_H, "label": node.label, "path": node.path,
              "count": sum(1 for _, d in node.items if d == 0), "talks": 0,
              "live": live, "collapsed": folded, "mode": "",
              "age": age_text(ctx.now - last) if last else "",
              "_last": last, "_keys": [(t["key"], d) for t, d in node.items]}
        nodes.append(me)
        # the root and big folders grow a trunk: branches hang below, slightly indented, instead
        # of starting past the pill's right end, so wide trees keep to the left
        trunk = has_children and not folded and (node is top or len(node.children) >= TRUNK_MIN)
        if trunk:
            own_top = cursor
            cursor += PILL_H + V_GAP
            kid_x, x1 = x + TRUNK_INDENT, x + TRUNK_X
        else:
            kid_x, x1 = x + w + H_GAP, x + w
        if has_children and not folded:
            center = None
            for ch in node.children:
                c, ch_live = place(ch, kid_x)
                if center is None:
                    center = c
                target = ch.children[0].path if ch.kind == "grid" else ch.path
                links.append({"uid": "link:%s>%s" % (node.path, target), "kind": "link",
                              "x1": x1, "y1": None, "x2": kid_x, "y2": c, "trunk": trunk,
                              "live": 1 if ch_live else 0, "_parent": me,
                              "_keys": [(t["key"], d) for t, d in ch.items]})
        else:
            center = cursor + HEAD_H / 2
            cursor += HEAD_H + V_GAP
        if trunk:
            center = own_top + PILL_H / 2
        me["ny"] = center - PILL_H / 2
        return center, live

    place(top, 0.0)
    for l in links:
        parent = l.pop("_parent")
        l["y1"] = parent["ny"] + (PILL_H if l["trunk"] else PILL_H / 2)
    for item in nodes + links:
        for role in ROLE_DEFAULTS:
            item.setdefault(role, ROLE_DEFAULTS[role])
    width = max((n["nx"] + n["nw"] for n in nodes), default=0.0)
    height = max((n["ny"] + n["nh"] for n in nodes), default=0.0)
    return Layout(nodes, links, rows, width, height)


ROLE_DEFAULTS = {
    "uid": "", "kind": "", "nx": 0.0, "ny": 0.0, "nw": 0.0, "nh": 0.0, "label": "",
    "path": "", "count": 0, "live": 0, "collapsed": False, "mode": "", "talks": 0,
    "age": "", "x1": 0.0, "y1": 0.0, "x2": 0.0, "y2": 0.0, "trunk": False, "drop": False,
}
ROW_ROLES = ("uid", "key", "title", "meta", "tool", "live", "busy", "archived",
             "selected", "match", "old", "depth", "tip")


def restyle(shown, by_key, ctx):
    """Keep ``shown``'s geometry and row slots, refresh their data from ``by_key``.

    A slot whose talk is no longer visible becomes a placeholder; counts ignore
    placeholders.
    """
    rows = {}
    for path, slots in shown.rows.items():
        out = []
        for r in slots:
            t = by_key.get(r["uid"])
            out.append(row_data(t, r["depth"], ctx) if t else placeholder_row(r["uid"], r["depth"]))
        rows[path] = out
    nodes = []
    for n in shown.nodes:
        present = [(by_key[k], d) for k, d in n["_keys"] if k in by_key]
        live, last = _stats(present, ctx.now)
        m = dict(n)
        m["live"] = live
        m["age"] = age_text(ctx.now - last) if last else ""
        m["_last"] = last
        if n["kind"] == "card":
            m["count"] = len(present)
            m["talks"] = sum(1 for _, d in present if d == 0)
        else:
            m["count"] = sum(1 for _, d in present if d == 0)
        nodes.append(m)
    links = [dict(l) for l in shown.links]
    for l in links:
        l["live"] = 1 if any(by_key[k].get("live") for k, _ in l["_keys"] if k in by_key) else 0
    return Layout(nodes, links, rows, shown.width, shown.height, shown.shape)


def drop_target(layout, x, y):
    found = ""
    for n in layout.nodes:
        if n["nx"] <= x <= n["nx"] + n["nw"] and n["ny"] <= y <= n["ny"] + n["nh"]:
            found = n["path"]
    return found


def all_tree(talks, ctx):
    """Every folder and card path of the full (unfolded) tree, for fold/unfold all.

    The top node (the root, or the collapsed chain it starts with, such as the home folder)
    comes first in the folder list."""
    tops, kids = _attach(talks)
    if not tops:
        return [], []
    top = _build(_trie(tops), "", True, kids, ctx)
    dirs, cards = [], []

    def walk(n):
        if n.kind == "dir":
            dirs.append(n.path)
        elif n.kind == "card":
            cards.append(n.path)
        for c in n.children:
            walk(c)

    walk(top)
    return dirs, cards


def under(path, base):
    return base == "/" or path == base or path.startswith(base.rstrip("/") + "/")


# ------------------------------------------------------------------ activity

def activity_grid(talks, agents, today):
    """``agents``: [(name, label)] in the user's order; ``today``: a date."""
    first_this = today.replace(day=1)
    first_last = (first_this - _dt.timedelta(days=1)).replace(day=1)
    nxt = (first_this + _dt.timedelta(days=32)).replace(day=1)
    end_this = nxt - _dt.timedelta(days=1)
    start = first_last - _dt.timedelta(days=first_last.weekday())
    end = end_this + _dt.timedelta(days=6 - end_this.weekday())
    present = {t.get("tool") for t in talks}
    tools = [{"name": n, "label": l} for n, l in agents if n in present]
    names = [t["name"] for t in tools]
    per = {n: {} for n in names}
    for t in talks:
        tool = t.get("tool")
        if tool not in per:
            continue
        for iso, n in (t.get("days") or {}).items():
            if isinstance(n, (int, float)) and not isinstance(n, bool):
                per[tool][iso] = per[tool].get(iso, 0) + int(n)
    weeks, peak = [], {n: 1 for n in names}
    totals = {first_last.month: {n: 0 for n in names}, first_this.month: {n: 0 for n in names}}
    d = start
    while d <= end:
        week = []
        for _ in range(7):
            iso = d.isoformat()
            inside = first_last <= d <= end_this
            counts = {n: (per[n].get(iso, 0) if inside else 0) for n in names}
            if inside:
                for n, c in counts.items():
                    peak[n] = max(peak[n], c)
                    totals[d.month][n] += c
            week.append({"date": iso, "label": "%s %s %d" % (WEEKDAYS[d.weekday()], MONTHS[d.month - 1], d.day),
                         "inside": inside, "future": d > today, "today": d == today, "n": counts})
            d += _dt.timedelta(days=1)
        weeks.append(week)
    months = [{"name": MONTHS[m.month - 1], "week": (m - start).days // 7, "totals": totals[m.month]}
              for m in (first_last, first_this)]
    return {"weeks": weeks, "months": months, "peak": peak, "tools": tools}


# ------------------------------------------------------------------ usage

def until_text(seconds):
    m = int(max(0, seconds) // 60)
    if m >= 1440:
        return "%dd %dh" % (m // 1440, (m % 1440) // 60)
    if m >= 60:
        return "%dh %dm" % (m // 60, m % 60)
    return "%dm" % m


def usage_note(u, now):
    if u.get("error"):
        return str(u["error"])
    at = u.get("observed_at")
    if not isinstance(at, (int, float)) or now - at <= 3600:
        return ""
    lt = time.localtime(at)
    if now - at < DAY:
        return time.strftime("as of %H:%M", lt)
    return "as of %s %s" % (WEEKDAYS[lt.tm_wday], time.strftime("%H:%M", lt))


def _round(x):
    """Half up, like QML's Math.round (Python's round() goes to even)."""
    return int(math.floor(x + 0.5))


def usage_bar(b, now):
    used = b.get("used") or 0
    window = b.get("window") or 0
    resets = b.get("resets_at")
    if isinstance(resets, (int, float)) and window:
        pace = min(1.0, max(0.0, (resets - now) / window))
    else:
        pace = -1.0
    if b.get("stale"):
        text = "reset"
    elif isinstance(resets, (int, float)):
        text = "↻ " + until_text(resets - now)
    else:
        text = ""
    left = max(0.0, 100 - float(used))
    return {"label": str(b.get("label") or ""), "used": _round(100 - left),
            "left": _round(left), "pace": pace, "resets": text}


def usage_groups(data, agents, now):
    """``agents``: [(name, label)] in display order → groups present in ``data``."""
    out = []
    for name, label in agents:
        u = data.get(name) if isinstance(data, dict) else None
        if not isinstance(u, dict):
            continue
        out.append({"name": label, "key": name,
                    "plan": str(u.get("plan") or "").replace("_", " "),
                    "error": usage_note(u, now),
                    "bars": [usage_bar(b, now) for b in u.get("bars") or [] if isinstance(b, dict)]})
    return out


# ------------------------------------------------------------------ settings

def agent_order(saved, agent_names):
    """The user's agent order: saved known names first, then any new ones in registry order."""
    out = []
    for n in saved if isinstance(saved, list) else ():
        if n in agent_names and n not in out:
            out.append(n)
    return out + [n for n in agent_names if n not in out]


def reorder_subset(order, names):
    """``names``, a reordering of some of ``order``, takes the slots those names held."""
    names = [n for i, n in enumerate(names) if n in order and n not in names[:i]]
    picked = set(names)
    it = iter(names)
    return [next(it) if n in picked else n for n in order]


def load_settings(raw, agent_names):
    """Validate parsed ui.json content → (filters, dirs, cards, agent order)."""
    filters = default_filters(agent_names)
    dirs, cards = {}, {}
    if not isinstance(raw, dict):
        return filters, dirs, cards, list(agent_names)
    saved = raw.get("filters")
    if isinstance(saved, dict):
        for k, v in saved.items():
            if k == "q" or k not in filters or type(v) is not type(filters[k]):
                continue
            filters[k] = v
    if filters["sort"] not in SORTS:
        filters["sort"] = "recent"
    if filters["age"] < 0:
        filters["age"] = 30
    d = raw.get("dirs")
    if isinstance(d, dict):
        dirs = {k: v for k, v in d.items() if isinstance(v, bool)}
    c = raw.get("cards")
    if isinstance(c, dict):
        cards = {k: v for k, v in c.items() if v in CARD_MODES and isinstance(v, str)}
    return filters, dirs, cards, agent_order(raw.get("order"), agent_names)


def settings_payload(filters, dirs, cards, order):
    return {"filters": {k: v for k, v in filters.items() if k != "q"},
            "dirs": dict(dirs), "cards": dict(cards), "order": list(order)}


def expand(path):
    return os.path.abspath(os.path.expanduser(str(path or "").strip()))


def is_dir(path):
    return os.path.isdir(os.path.expanduser(str(path or "").strip())) if str(path or "").strip() else False
