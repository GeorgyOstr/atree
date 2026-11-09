"""Window-free presentation logic (ui.md §4, §5, §11.2, §11.6, §12.1, §14.2)."""
import datetime
import json
import os
import random
import tempfile
import unittest
from unittest import mock

import ui_support as S
from atree.qt import layout as L
from atree.qt import widget as W

NOW = S.NOW
DAY = 86400
AGENTS = ["claude", "codex", "gemini", "opencode", "copilot"]


def ctx(**kw):
    filters = L.default_filters(AGENTS)
    filters.update(kw.pop("filters", {}))
    return L.Ctx(filters, NOW, kw.pop("home", "/home/u"), **kw)


def lay(talks, **kw):
    c = ctx(**kw)
    return L.compute_layout(L.visible_talks(talks, c.filters, NOW, c.ai_keys), c)


def node(layout, uid):
    return next(n for n in layout.nodes if n["uid"] == uid)


class VisibilityTest(unittest.TestCase):
    def test_filters(self):
        talks = [S.talk("a", "/p", NOW - 1), S.talk("old", "/p", NOW - 40 * DAY),
                 S.talk("oldlive", "/p", NOW - 40 * DAY, live=True),
                 S.talk("arch", "/p", NOW, archived=True), S.talk("cx", "/p", NOW, tool="codex")]
        f = L.default_filters(AGENTS)
        keys = lambda **kw: {t["id"] for t in L.visible_talks(talks, dict(f, **kw), NOW)}
        self.assertEqual(keys(), {"a", "oldlive", "cx"})
        self.assertEqual(keys(codex=False), {"a", "oldlive"})
        self.assertEqual(keys(archived=True), {"a", "oldlive", "arch", "cx"})
        self.assertEqual(keys(live=True), {"oldlive"})
        self.assertEqual(keys(age=0), {"a", "old", "oldlive", "cx"})

    def test_query_keeps_parents(self):
        talks = [S.talk("p", "/p", NOW, title="parent"),
                 S.talk("c", "/q", NOW, title="Needle child", parent="claude:p"),
                 S.talk("x", "/p", NOW, title="other", branch="feature-NEEDLE")]
        f = dict(L.default_filters(AGENTS), q="  needle ")
        self.assertEqual({t["id"] for t in L.visible_talks(talks, f, NOW)}, {"p", "c", "x"})

    def test_ai_results_ignore_filters(self):
        talks = [S.talk("a", "/p", NOW - 400 * DAY, archived=True), S.talk("b", "/p", NOW, tool="codex"),
                 S.talk("c", "/p", NOW)]
        f = dict(L.default_filters(AGENTS), codex=False)
        vis = L.visible_talks(talks, f, NOW, {"claude:a", "codex:b"})
        self.assertEqual([t["id"] for t in vis], ["a"])


class TreeTest(unittest.TestCase):
    def test_chain_compression_and_labels(self):
        out = lay([S.talk("a", "/home/u/w/one", NOW), S.talk("b", "/home/u/w/two", NOW)])
        top = out.nodes[0]
        self.assertEqual((top["kind"], top["label"], top["path"]), ("dir", "~/w", "/home/u/w"))
        out = lay([S.talk("a", "/home/u", NOW), S.talk("b", "/home/u/x", NOW)])
        self.assertEqual(out.nodes[0]["label"], "~")
        self.assertEqual({n["uid"] for n in out.nodes}, {"dir:/home/u", "card:/home/u/x", "card:/home/u"})
        own = node(out, "card:/home/u")
        self.assertEqual(own["label"], "")
        out = lay([S.talk("a", "/srv/a/b", NOW), S.talk("b", "/opt/x", NOW)])
        self.assertEqual(sorted(n["label"] for n in out.nodes), ["/", "opt/x", "srv/a/b"])

    def test_lay5_busy_subagent_lifts_folder(self):
        talks = [S.talk("old", "/w/a", NOW - 9 * DAY), S.talk("new", "/w/b", NOW - DAY),
                 S.talk("sub", "/elsewhere", NOW - 9 * DAY, live=True, parent="claude:old")]
        out = lay(talks)
        cards = [n["path"] for n in out.nodes if n["kind"] == "card"]
        self.assertEqual(cards, ["/w/a", "/w/b"])
        out = lay(talks, filters={"reverse": True})
        self.assertEqual([n["path"] for n in out.nodes if n["kind"] == "card"], ["/w/b", "/w/a"])

    def test_lay8_counts(self):
        talks = [S.talk("p", "/w/a", NOW - 50, live=True), S.talk("q", "/w/a", NOW - 60),
                 S.talk("s1", "/far/away", NOW - 10, parent="claude:p", live=True),
                 S.talk("s2", "/w/a", NOW - 5, parent="claude:s1"),
                 S.talk("r", "/w/b", NOW - 70)]
        out = lay(talks)
        a = node(out, "card:/w/a")
        self.assertEqual((a["talks"], a["count"], a["live"]), (2, 4, 2))
        self.assertEqual([(r["key"], r["depth"]) for r in out.rows["/w/a"]],
                         [("claude:p", 0), ("claude:s1", 1), ("claude:s2", 2), ("claude:q", 0)])
        w = node(out, "dir:/w")
        self.assertEqual(w["count"], 3)
        self.assertEqual(w["count"], sum(n["talks"] for n in out.nodes if n["kind"] == "card"))
        self.assertEqual(w["live"], 2)
        self.assertNotIn("dir:/far", {n["uid"] for n in out.nodes})
        self.assertEqual(w["age"], "now")

    def test_lay9_ties_do_not_depend_on_scan_order(self):
        talks = [S.talk(k, "/w/%s" % d, NOW - 100, created=NOW - 500) for k, d in
                 [("a", "x"), ("b", "x"), ("c", "y"), ("d", "y"), ("e", "z"), ("f", "z")]]
        talks += [S.talk("s%d" % i, "/w/x", NOW - 100, created=NOW - 500, parent="claude:a") for i in range(3)]
        rnd = random.Random(7)
        first = None
        for sort in L.SORTS:
            for _ in range(6):
                rnd.shuffle(talks)
                out = lay(list(talks), filters={"sort": sort})
                sig = (out.shape, json.dumps(out.rows, sort_keys=True))
                if first is None or first[0] != sort:
                    first = (sort, sig)
                self.assertEqual(sig, first[1])

    def test_lay11_recent_grandchild_keeps_ancestors_open(self):
        talks = [S.talk("g", "/w/a/deep", NOW - 60 * DAY), S.talk("h", "/w/a/other", NOW - 60 * DAY),
                 S.talk("x", "/w/b", NOW - 60 * DAY), S.talk("y", "/w/c", NOW - 60 * DAY)]
        out = lay(talks, filters={"age": 0})
        self.assertTrue(node(out, "dir:/w")["collapsed"])
        self.assertTrue(node(lay(talks, dirs={"/w": False}, filters={"age": 0}), "dir:/w/a")["collapsed"])
        talks += [S.talk("c1", "/q", NOW - 60 * DAY, parent="claude:g"),
                  S.talk("c2", "/q", NOW - 60, parent="claude:c1")]
        out = lay(talks, filters={"age": 0})
        self.assertFalse(node(out, "dir:/w/a")["collapsed"])
        self.assertFalse(node(out, "dir:/w")["collapsed"])
        talks[-1] = S.talk("c2", "/q", NOW - 60 * DAY, live=True, parent="claude:c1")
        self.assertFalse(node(lay(talks, filters={"age": 0}), "dir:/w/a")["collapsed"])

    def test_root_and_big_folders_grow_a_trunk_small_ones_branch_sideways(self):
        talks = [S.talk(k + s, "/w/p/%s/%s" % (k, s), NOW - i) for i, (k, s) in enumerate(
                     [("x", "m"), ("x", "n"), ("y", "m"), ("y", "n"), ("z", "m"), ("z", "n")])]
        talks += [S.talk("d", "/w/q/r/d", NOW - 10), S.talk("e", "/w/q/s/e", NOW - 11)]
        out = lay(talks, cards={})
        root, p, q = node(out, "dir:/w"), node(out, "dir:/w/p"), node(out, "dir:/w/q")
        # the root: pill on top, branches below it, indented, fed from under its chevron
        self.assertEqual((root["ny"], p["nx"], q["nx"]), (0, L.TRUNK_INDENT, L.TRUNK_INDENT))
        self.assertEqual(p["ny"], L.PILL_H + L.V_GAP)              # a trunk: pill tops its slot
        to_p = next(l for l in out.links if l["uid"] == "link:/w>/w/p")
        self.assertTrue(to_p["trunk"])
        self.assertEqual((to_p["x1"], to_p["y1"], to_p["x2"], to_p["y2"]),
                         (L.TRUNK_X, L.PILL_H, p["nx"], p["ny"] + L.PILL_H / 2))
        # /w/p has three folder branches: a trunk too; /w/q has one (a two-card grid): sideways
        x = node(out, "dir:/w/p/x")
        self.assertEqual(x["nx"], p["nx"] + L.TRUNK_INDENT)
        self.assertTrue(next(l for l in out.links if l["uid"] == "link:/w/p>/w/p/x")["trunk"])
        dcard = node(out, "card:/w/q/r/d")
        self.assertEqual(dcard["nx"], q["nx"] + q["nw"] + L.H_GAP)
        self.assertEqual(q["ny"] + L.PILL_H / 2, dcard["ny"] + L.HEAD_H / 2)
        side = next(l for l in out.links if l["uid"] == "link:/w/q>/w/q/r/d")
        self.assertFalse(side["trunk"])
        self.assertEqual(side["y1"], q["ny"] + L.PILL_H / 2)
        self.assertTrue(all(n["ny"] >= 0 for n in out.nodes))
        folded = lay(talks, dirs={"/w": True})
        self.assertFalse(any(l["trunk"] for l in folded.links))

    def test_geometry(self):
        talks = [S.talk("t%d" % i, "/w/a", NOW - i) for i in range(9)] + [S.talk("b", "/w/b", NOW)]
        out = lay(talks)
        a, b = node(out, "card:/w/a"), node(out, "card:/w/b")
        self.assertEqual(a["mode"], "compact")
        self.assertEqual(a["nh"], 30 + 5 * 26 + 8)
        self.assertEqual(b["nh"], 30 + 26 + 8)
        self.assertEqual((a["nw"], b["nx"] - a["nx"]), (430, 442))
        w = node(out, "dir:/w")
        self.assertEqual(a["nx"], w["nx"] + L.TRUNK_INDENT)       # the root's trunk
        self.assertEqual((w["ny"], a["ny"]), (0, 28 + 10))
        link, = [l for l in out.links if l["uid"].startswith("link:/w>")]
        self.assertEqual(link["uid"], "link:/w>/w/a")  # one link per grid, to its first card
        self.assertEqual((link["x1"], link["y1"], link["x2"]), (w["nx"] + L.TRUNK_X, w["ny"] + 28, a["nx"]))
        out = lay(talks, cards={"/w/a": "full"})
        self.assertEqual(node(out, "card:/w/a")["nh"], 30 + 9 * 26 + 8)
        out = lay(talks, cards={"/w/a": "collapsed"})
        self.assertEqual(node(out, "card:/w/a")["nh"], 30)
        self.assertLess(node(out, "card:/w/a")["nw"], 430)
        self.assertEqual(out.width, max(n["nx"] + n["nw"] for n in out.nodes))

    def test_grid_columns_fixed(self):
        talks = [S.talk("t%d" % i, "/w/c%d" % i, NOW - i) for i in range(7)]
        out = lay(talks)
        cards = [n for n in out.nodes if n["kind"] == "card"]
        xs = sorted({n["nx"] for n in cards})
        self.assertEqual(len(xs), 3)
        self.assertEqual(len([l for l in out.links]), 1)

    def test_age_text(self):
        cases = [(0, "now"), (59, "now"), (60, "1m"), (3599, "59m"), (3600, "1h"), (DAY - 1, "23h"),
                 (DAY, "1d"), (14 * DAY - 1, "13d"), (14 * DAY, "2w"), (60 * DAY - 1, "8w"), (60 * DAY, "2mo")]
        for s, want in cases:
            self.assertEqual(L.age_text(s), want, s)

    def test_meta(self):
        t = S.talk("a", "/p", NOW - 120, live=True, live_status="busy", prompts=4, context=34000,
                   tokens=1_200_000, agents=2, related=[{"key": "x"}], branch="feat", archived=False)
        self.assertEqual(L.row_meta(t, "context", NOW), "busy · 2m · 4p · 34k ctx · 2 agents · ⑂ · ⎇ feat")
        self.assertEqual(L.row_meta(dict(t, live=False, branch="main", agents=1, related=[]), "tokens", NOW),
                         "2m · 4p · 1.2M tok · 1 agent")

    def test_placeholder_row(self):
        r = L.placeholder_row("claude:gone", 0)
        self.assertEqual((r["key"], r["title"], r["meta"], r["tip"]), ("", "No longer in view", "", ""))


class RanksTest(unittest.TestCase):
    def test_new_running_goes_front_and_stays(self):
        r = L.Ranks()
        a, b = S.talk("a", "/p", NOW - 10), S.talk("b", "/p", NOW - 5)
        r.update([a, b], 0)
        n = S.talk("n", "/p", NOW - 1000, live=True)
        r.update([a, b, n], 4)
        self.assertLess(r.get("claude:n"), min(r.get("claude:a"), r.get("claude:b")))
        rank = r.get("claude:n")
        for i in range(5):
            r.update([a, b, dict(n, live=i % 2 == 0)], 8 + 4 * i)
            self.assertEqual(r.get("claude:n"), rank)

    def test_one_front_per_scan(self):
        r = L.Ranks()
        a, b, c = S.talk("a", "/p", NOW - 10), S.talk("b", "/p", NOW - 50), S.talk("c", "/p", NOW - 90)
        r.update([a, b, c], 0)
        front = min(r.as_dict().values()) - 1
        lb, lc = dict(b, live=True), dict(c, live=True)
        r.update([a, lb, lc], 1)
        r.update([a, lb, lc], 10)                 # both promote in the same scan, against one front
        self.assertEqual((r.get("claude:b"), r.get("claude:c")),
                         (min(L.natural_recent(lb), front), min(L.natural_recent(lc), front)))
        # a natural key (live bonus) that beats the front wins
        r = L.Ranks()
        r.update([a, b], 0)
        far = dict(b, live=True, updated=NOW + 10 ** 6)
        r.update([a, far], 1)
        r.update([a, far], 10)
        self.assertEqual(r.get("claude:b"), L.natural_recent(far))


class ActivityTest(unittest.TestCase):
    def test_week_structure_without_talks(self):
        g = L.activity_grid([], [("claude", "Claude")], datetime.date(2026, 10, 15))
        self.assertEqual(g["tools"], [])
        first = g["weeks"][0][0]
        self.assertEqual(first["date"], "2026-08-31")
        self.assertEqual(g["weeks"][-1][-1]["date"], "2026-11-01")
        self.assertTrue(all(len(w) == 7 for w in g["weeks"]))
        self.assertFalse(first["inside"])
        self.assertTrue(g["weeks"][0][1]["inside"])
        self.assertEqual([m["name"] for m in g["months"]], ["Sep", "Oct"])
        self.assertEqual(g["months"][0]["week"], 0)

    def test_counts(self):
        talks = [S.talk("a", "/p", NOW, days={"2026-10-06": 3, "2026-09-01": 2, "2026-07-01": 9}),
                 S.talk("b", "/p", NOW, tool="codex", days={"2026-10-06": 1})]
        g = L.activity_grid(talks, [("claude", "Claude"), ("codex", "Codex")], datetime.date(2026, 10, 15))
        day = next(d for w in g["weeks"] for d in w if d["date"] == "2026-10-06")
        self.assertEqual(day["label"], "Tue Oct 6")
        self.assertEqual(day["n"], {"claude": 3, "codex": 1})
        self.assertEqual(g["peak"], {"claude": 3, "codex": 1})
        self.assertEqual(g["months"][0]["totals"], {"claude": 2, "codex": 0})
        self.assertEqual([t["name"] for t in g["tools"]], ["claude", "codex"])
        today = next(d for w in g["weeks"] for d in w if d["today"])
        self.assertEqual(today["date"], "2026-10-15")
        self.assertTrue(next(d for w in g["weeks"] for d in w if d["date"] == "2026-10-16")["future"])


class UsageTest(unittest.TestCase):
    def test_pan18_note(self):
        lt = lambda t: __import__("time").localtime(t)
        self.assertEqual(L.usage_note({"error": "boom", "observed_at": NOW - 9 * 3600}, NOW), "boom")
        self.assertEqual(L.usage_note({"observed_at": NOW - 600}, NOW), "")
        at = NOW - 2 * 3600
        self.assertEqual(L.usage_note({"observed_at": at}, NOW), "as of %02d:%02d" % (lt(at).tm_hour, lt(at).tm_min))
        at = NOW - 2 * DAY
        note = L.usage_note({"observed_at": at}, NOW)
        self.assertRegex(note, r"^as of (Mon|Tue|Wed|Thu|Fri|Sat|Sun) \d\d:\d\d$")

    def test_bars(self):
        g = L.usage_groups({"codex": {"plan": "pro_lite", "bars": [
            {"label": "week", "used": 130, "window": 1000, "resets_at": NOW + 250},
            {"label": "x", "used": 10, "window": 0, "resets_at": None},
            {"label": "s", "used": 0, "window": 100, "resets_at": NOW - 5, "stale": True},
            {"label": "d", "used": 1, "window": 10 ** 6, "resets_at": NOW + DAY + 2 * 3600}]},
            "at": NOW}, [("claude", "Claude"), ("codex", "Codex")], NOW)
        self.assertEqual(len(g), 1)
        self.assertEqual((g[0]["name"], g[0]["key"], g[0]["plan"]), ("Codex", "codex", "pro lite"))
        bars = g[0]["bars"]
        self.assertEqual((bars[0]["left"], bars[0]["pace"], bars[0]["resets"]), (0, 0.25, "↻ 4m"))
        self.assertEqual((bars[1]["pace"], bars[1]["resets"]), (-1.0, ""))
        self.assertEqual(bars[2]["resets"], "reset")
        self.assertEqual(bars[3]["resets"], "↻ 1d 2h")


class SettingsTest(unittest.TestCase):
    def test_set1_robust_loading(self):
        for raw in (None, [], "x", 3, {"filters": [], "dirs": "x", "cards": 1}):
            f, d, c, o = L.load_settings(raw, AGENTS)
            self.assertEqual(f["age"], 30)
            self.assertEqual((d, c, o), ({}, {}, list(AGENTS)))

    def test_set2_types_and_values(self):
        f, _, _, _ = L.load_settings({"filters": {
            "claude": 0, "codex": False, "live": 1, "age": True, "sort": "weird", "reverse": "yes",
            "search": "nobody", "q": "kept?", "unknown": 1}}, AGENTS)
        self.assertEqual((f["claude"], f["codex"], f["live"], f["age"], f["sort"], f["reverse"], f["q"]),
                         (True, False, False, 30, "recent", False, ""))
        self.assertNotIn("unknown", f)
        self.assertNotIn("search", f)  # the old search-mode setting is dropped
        self.assertEqual(L.load_settings({"filters": {"age": 7.0}}, AGENTS)[0]["age"], 30)
        self.assertEqual(L.load_settings({"filters": {"age": -3}}, AGENTS)[0]["age"], 30)
        self.assertEqual(L.load_settings({"filters": {"age": 0}}, AGENTS)[0]["age"], 0)

    def test_set3_overrides(self):
        _, d, c, _ = L.load_settings({"dirs": {"/a": True, "/b": "yes", "/c": 0, "/d": False},
                                   "cards": {"/a": "full", "/b": "huge", "/c": True, "/d": "collapsed"}}, AGENTS)
        self.assertEqual(d, {"/a": True, "/d": False})
        self.assertEqual(c, {"/a": "full", "/d": "collapsed"})

    def test_set4_round_trip(self):
        f = L.default_filters(AGENTS)
        f.update(gemini=False, live=True, archived=True, age=0, sort="name", reverse=True, q="zz")
        order = list(reversed(AGENTS))
        raw = json.loads(json.dumps(L.settings_payload(f, {"/x": True}, {"/y": "full"}, order)))
        f2, d, c, o = L.load_settings(raw, AGENTS)
        self.assertEqual(f2, dict(f, q=""))
        self.assertEqual((d, c, o), ({"/x": True}, {"/y": "full"}, order))

    def test_set5_agent_order(self):
        names = ["claude", "codex", "gemini"]
        # unknown and repeated names dropped, agents missing from the file appended in registry order
        self.assertEqual(L.agent_order(["gemini", "nobody", "gemini", 3], names), ["gemini", "claude", "codex"])
        self.assertEqual(L.agent_order("x", names), names)
        self.assertEqual(L.load_settings({"order": ["codex"]}, names)[3], ["codex", "claude", "gemini"])
        # a reordered subset swaps among its own slots; the rest stay put
        self.assertEqual(L.reorder_subset(["a", "b", "c", "d"], ["d", "b"]), ["a", "d", "c", "b"])
        self.assertEqual(L.reorder_subset(["a", "b", "c"], ["c", "zz", "a", "c"]), ["c", "b", "a"])


class WidgetMaskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def _gs(self, **values):
        return lambda key: values.get(key, "")

    def test_wid4_icon_count(self):
        no_positions = mock.patch.object(W, "icon_positions", return_value=())
        no_positions.start()
        self.addCleanup(no_positions.stop)
        desk = os.path.join(self.tmp, "Bureau")
        os.makedirs(desk)
        for name in ("a.txt", "b", ".hidden"):
            open(os.path.join(desk, name), "w").close()
        gs = self._gs(**{"start-corner": "top-left", "icon-size": "small", "show-home": "true", "show-trash": "false"})
        with mock.patch.dict(os.environ, {"XDG_DESKTOP_DIR": desk}), mock.patch.object(W, "gsetting", gs), \
                mock.patch.object(W, "platform_desktop", side_effect=AssertionError("consulted")):
            self.assertEqual(W.ding_state(), ("top-left", "small", 3, ()))
        env = {k: v for k, v in os.environ.items() if k != "XDG_DESKTOP_DIR"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch.object(W, "gsetting", gs), \
                mock.patch.object(W, "platform_desktop", return_value=desk):
            self.assertEqual(W.ding_state(), ("top-left", "small", 3, ()))
        with mock.patch.dict(os.environ, {"XDG_DESKTOP_DIR": os.path.join(self.tmp, "missing")}), \
                mock.patch.object(W, "gsetting", gs):
            self.assertEqual(W.ding_state(), ("top-left", "small", 1, ()))
        only_hidden = os.path.join(self.tmp, "hid")
        os.makedirs(only_hidden)
        open(os.path.join(only_hidden, ".x"), "w").close()
        gs0 = self._gs(**{"start-corner": "top-left", "show-home": "false", "show-trash": "false"})
        with mock.patch.dict(os.environ, {"XDG_DESKTOP_DIR": only_hidden}), mock.patch.object(W, "gsetting", gs0):
            self.assertIsNone(W.ding_state())
        with mock.patch.object(W, "gsetting", self._gs()):
            self.assertIsNone(W.ding_state())

    def test_placed_icons_are_read_from_gio_metadata(self):
        out = ("uri: file:///d/a.pdf\n  metadata::nautilus-icon-position: 1524,849\n"
               "uri: file:///d/b\nuri: file:///d/c\n  metadata::nautilus-icon-position: bad\n")
        with mock.patch.object(W.subprocess, "run", return_value=mock.Mock(stdout=out)) as run:
            self.assertEqual(W.icon_positions(["/d/a.pdf", "/d/b", "/d/c"]), ((1524, 849),))
        self.assertEqual(run.call_args.args[0][:4], ["gio", "info", "-a", "metadata::nautilus-icon-position"])
        self.assertEqual(W.icon_positions([]), ())

    def test_placed_icons_become_holes_in_window_coordinates(self):
        state = ("bottom-right", "standard", 2, ((1524, 849),))
        rects = W.icon_rects(state, 1854, 1048, origin=(66, 32))
        # one icon is placed by hand, so DING stacks only the other one in the corner
        self.assertEqual(rects[0], W.icon_hole(("bottom-right", "standard", 1), 1854, 1048))
        self.assertEqual(rects[1], (1524 - 66 - 8, 849 - 32 - 8, 136, 122))

    def test_wid5_hole(self):
        self.assertEqual(W.icon_hole(("top-left", "standard", 3), 1000, 800), (0, 0, 136, 366))
        self.assertEqual(W.icon_hole(("bottom-right", "tiny", 20), 1000, 800), (1000 - 3 * 86, 800 - 8 * 96, 258, 768))
        self.assertEqual(W.icon_hole(("top-right", "bogus", 1), 500, 50), (364, 0, 136, 122))
        self.assertIsNone(W.icon_hole(None, 10, 10))


if __name__ == "__main__":
    unittest.main()
