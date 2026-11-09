"""Backend behaviour without a window (ui.md §4–§12, core.md §7)."""
import json
import os
import tempfile
import unittest
from unittest import mock

import ui_support as S
from atree import ops
from atree.qt import layout as L
from atree.qt import tasks as tasks_mod

NOW = S.NOW


class Recorder:
    """Counts backend signals and model change notifications."""

    def __init__(self, b, *models):
        self.counts = {}
        self.toasts = []
        self.undos = []
        for name in ("changed", "usageChanged", "detailsChanged", "tasksChanged", "aiChanged"):
            getattr(b, name).connect(lambda *a, n=name: self.bump(n))
        b.toast.connect(lambda m, e: self.toasts.append((m, e)))
        b.undoable.connect(lambda m, k, e: self.undos.append((m, list(k), e)))
        for m in (b.nodeModel,) + models:
            for sig in ("rowsInserted", "rowsRemoved", "rowsMoved", "dataChanged", "modelReset"):
                getattr(m, sig).connect(lambda *a, n=sig: self.bump("model"))

    def bump(self, name):
        self.counts[name] = self.counts.get(name, 0) + 1

    def __getitem__(self, name):
        return self.counts.get(name, 0)

    def reset(self):
        self.counts.clear()
        del self.toasts[:]
        del self.undos[:]


def settings_file():
    fd, path = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    os.unlink(path)
    return path


def backend():
    return S.make_backend(settings_path=settings_file())


def row_keys(b, path):
    return [r["key"] for r in b.cardModel(path).items()]


def card_order(b):
    return [n["path"] for n in b._nodes.items() if n["kind"] == "card"]


def geom(b, uid):
    n = next(n for n in b._nodes.items() if n["uid"] == uid)
    return (n["nx"], n["ny"], n["nw"], n["nh"])


class ScanTest(unittest.TestCase):
    def test_scan3_errors_toast_once(self):
        b = backend()
        rec = Recorder(b)
        b.collector = mock.Mock(return_value=([], []))
        t = [S.talk("a", "/p", NOW)]
        b.apply_scan(t, ["scanning Codex failed: x", "scanning Gemini failed: y"])
        b.apply_scan(t, ["scanning Codex failed: x", "scanning Gemini failed: y", "z"])
        self.assertEqual([m for m, e in rec.toasts],
                         ["scanning Codex failed: x", "scanning Gemini failed: y", "z"])
        self.assertTrue(all(e for _, e in rec.toasts))
        rec.reset()
        b.apply_scan(None, failure="bad index")
        b.apply_scan(None, failure="bad index")
        self.assertEqual(rec.toasts, [("scan failed: bad index", True)])
        self.assertEqual([t["key"] for t in b.talks], ["claude:a"])
        S.pump(50)
        b.collector.assert_not_called()

    def test_scan1_one_queued_rescan(self):
        b = backend()
        calls = []
        b.collector = lambda: calls.append(1) or ([], [])
        b._scanning = True
        b.refresh()
        b.refresh()
        b.apply_scan([], [])
        S.pump(200)
        self.assertEqual(len(calls), 1)
        b.shutdown()

    def test_lay23_unchanged_scan_is_silent(self):
        b = backend()
        talks = [S.talk("a", "/w/x", NOW - 10, live=True), S.talk("b", "/w/y", NOW - 20),
                 S.talk("c", "/w/y", NOW - 30, parent="claude:b")]
        b.apply_scan(talks)
        m1, m2 = b.cardModel("/w/x"), b.cardModel("/w/y")
        rec = Recorder(b, m1, m2)
        for _ in range(3):
            b.apply_scan([dict(t) for t in talks])
        b.holdLayout(True)
        b.apply_scan([dict(t) for t in talks])
        b.holdLayout(False)
        self.assertEqual(rec.counts, {})

    def test_repeated_key_does_not_corrupt_models(self):
        # e.g. a talk caught mid-archive: still scanned and already in the index
        b = backend()
        b.setFilter("archived", True)
        t = S.talk("a", "/p", NOW - 10)
        talks = [t, S.talk("b", "/p", NOW - 20), dict(t, archived=True)]
        for _ in range(2):
            b.apply_scan(talks)
            self.assertEqual(row_keys(b, "/p"), ["claude:a", "claude:b"])

    def test_collect_prefers_scanned_talk_over_index_entry(self):
        from atree.qt import backend as backend_mod
        t = S.talk("a", "/p", NOW)
        index = {"claude:a": dict(t, archive="/x.tar.gz"), "claude:b": dict(S.talk("b", "/p", NOW))}
        with mock.patch("atree.scan.scan_all", return_value=[dict(t)]), \
                mock.patch.object(ops, "archived", return_value=index):
            talks, _ = backend_mod.collect()
        self.assertEqual(sorted((x["key"], x["archived"]) for x in talks),
                         [("claude:a", False), ("claude:b", True)])

    def test_stats(self):
        b = backend()
        b.apply_scan([S.talk("a", "/p", NOW, live=True), S.talk("b", "/p", NOW),
                      S.talk("c", "/p", NOW, archived=True)])
        self.assertEqual(b.stats, "2 talks · 1 running · 3 total")


class HoldTest(unittest.TestCase):
    def setUp(self):
        self.b = backend()
        self.talks = [S.talk("a", "/w/x", NOW - 10), S.talk("b", "/w/x", NOW - 20),
                      S.talk("c", "/w/y", NOW - 30), S.talk("d", "/w/y", NOW - 40)]
        self.b.apply_scan(self.talks)

    def test_stab3_4_6_placeholder_and_release(self):
        b = self.b
        before = geom(b, "card:/w/x")
        b.holdLayout(True)
        b.apply_scan([t for t in self.talks if t["id"] != "b"])
        rows = b.cardModel("/w/x").items()
        self.assertEqual([r["uid"] for r in rows], ["claude:a", "claude:b"])
        self.assertEqual((rows[1]["key"], rows[1]["title"], rows[1]["live"]), ("", "No longer in view", False))
        x = next(n for n in b._nodes.items() if n["uid"] == "card:/w/x")
        self.assertEqual((x["talks"], x["count"]), (1, 1))
        self.assertEqual(geom(b, "card:/w/x"), before)
        self.assertTrue(b.layoutPending)
        b.apply_scan(self.talks)
        self.assertEqual(row_keys(b, "/w/x"), ["claude:a", "claude:b"])
        self.assertFalse(b.layoutPending)
        b.apply_scan([t for t in self.talks if t["id"] != "b"])
        self.assertTrue(b.layoutPending)
        b.holdLayout(False)
        self.assertFalse(b.layoutPending)
        self.assertEqual(row_keys(b, "/w/x"), ["claude:a"])

    def test_stab3_metadata_refreshes_while_held(self):
        b = self.b
        b.holdLayout(True)
        changed = [dict(t) for t in self.talks]
        changed[1]["title"] = "renamed"
        changed[1]["live"] = True
        changed[1]["live_status"] = "busy"
        b.apply_scan(changed)
        row = b.cardModel("/w/x").items()[1]
        self.assertEqual((row["title"], row["live"], row["busy"]), ("renamed", True, True))
        self.assertEqual(next(n for n in b._nodes.items() if n["uid"] == "card:/w/x")["live"], 1)

    def test_stab5_depth_change_is_shape(self):
        b = self.b
        b.holdLayout(True)
        changed = [dict(t) for t in self.talks]
        changed[1]["parent"] = "claude:a"
        b.apply_scan(changed)
        self.assertTrue(b.layoutPending)
        self.assertEqual([r["depth"] for r in b.cardModel("/w/x").items()], [0, 0])
        b.holdLayout(False)
        self.assertEqual([r["depth"] for r in b.cardModel("/w/x").items()], [0, 1])

    def test_stab7_select_while_held_keeps_layout(self):
        b = self.b
        b.holdLayout(True)
        new = self.talks + [S.talk("n", "/w/a", NOW, live=True)]
        b.apply_scan(new)
        before = geom(b, "card:/w/x")
        self.assertTrue(b.layoutPending)
        b.select("claude:c", False)
        self.assertTrue(b.layoutPending)
        self.assertEqual(geom(b, "card:/w/x"), before)
        self.assertTrue(b.cardModel("/w/y").items()[0]["selected"])

    def test_stab8_explicit_changes_apply_while_held(self):
        b = self.b
        b.holdLayout(True)
        b.apply_scan(self.talks + [S.talk("n", "/w/a", NOW, live=True)])
        self.assertTrue(b.layoutPending)
        b.toggleDir("/w")
        self.assertFalse(b.layoutPending)

    def test_stab22_after_operation_intent(self):
        b = self.b
        b.collector = lambda: (self.talks, [])
        b.holdLayout(True)
        b._scanning = True                     # a scan is in flight
        b.batch_done((["claude:b"], []), "moved %d talk(s)")
        self.assertTrue(b._after_op)
        b.apply_scan([t for t in self.talks if t["id"] != "b"])
        self.assertFalse(b.layoutPending)
        self.assertEqual(row_keys(b, "/w/x"), ["claude:a"])
        self.assertTrue(b._after_op)           # kept for the queued scan
        S.pump(200)
        self.assertFalse(b._after_op)
        b._scanning = True
        b.batch_done(([], ["failed"]), "moved %d talk(s)")
        self.assertFalse(b._after_op)
        b.shutdown()


class StickyRecentTest(unittest.TestCase):
    def setUp(self):
        self.clock = S.Clock()
        self.b = S.make_backend(self.clock, settings_file())
        self.z = S.talk("z", "/w/p", NOW - 10)
        self.a = S.talk("a", "/w/p", NOW - 100)

    def scan(self, *talks, dt=0):
        self.clock.advance(dt)
        self.b.apply_scan([dict(t) for t in talks])

    def test_stab18_example(self):
        b = self.b
        self.scan(self.z, self.a)
        b.applyLayout()
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])
        live_a = dict(self.a, live=True)
        self.scan(self.z, live_a, dt=4)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])
        self.scan(self.z, live_a, dt=4)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])
        self.scan(self.z, live_a, dt=4)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:a", "claude:z"])
        self.scan(self.z, self.a, dt=4)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:a", "claude:z"])
        b.applyLayout()
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])

    def test_stab15_alternating_never_reranks(self):
        b = self.b
        self.scan(self.z, self.a)
        for i in range(8):
            self.scan(self.z, dict(self.a, live=i % 2 == 0, updated=NOW + i), dt=4)
            self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])

    def test_stab16_fold_does_not_promote_early(self):
        b = self.b
        self.scan(self.z, self.a)
        live_a = dict(self.a, live=True)
        self.scan(self.z, live_a, dt=4)
        ranks = dict(b.ranks.as_dict())
        self.clock.advance(5)                  # 9 s of running, but no scan yet
        b.setCardMode("/w/p", "full")
        b.toggleDir("/w")
        b.toggleDir("/w")
        b.autoFold()
        self.assertEqual(b.ranks.as_dict(), ranks)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])
        self.scan(self.z, live_a)              # 5 s into the streak
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])
        self.scan(self.z, live_a, dt=3)        # 8 s
        self.assertEqual(row_keys(b, "/w/p"), ["claude:a", "claude:z"])

    def test_stab17_filter_change_resets(self):
        b = self.b
        self.scan(self.z, self.a)
        live_a = dict(self.a, live=True)
        self.scan(self.z, live_a, dt=4)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])
        b.setFilter("reverse", True)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:z", "claude:a"])   # exact keys, reversed
        b.setFilter("reverse", False)
        self.assertEqual(row_keys(b, "/w/p"), ["claude:a", "claude:z"])
        b.select("claude:z", False)
        b.toggleDir("/w")
        self.assertEqual(row_keys(b, "/w/p"), ["claude:a", "claude:z"])

    def test_stab13_new_running_talk_enters_at_front_and_stays(self):
        b = self.b
        self.scan(self.z, self.a)
        n = S.talk("n", "/w/p", NOW - 1000, live=True)
        self.scan(self.z, self.a, n, dt=4)
        self.assertEqual(row_keys(b, "/w/p")[0], "claude:n")
        for i in range(4):
            self.scan(self.z, self.a, dict(n, live=i % 2 == 1), dt=4)
            self.assertEqual(row_keys(b, "/w/p")[0], "claude:n")

    def test_stab19_new_folder_ordering(self):
        b = self.b
        old = S.talk("o", "/w/old", NOW - 500, live=True)
        other = S.talk("q", "/w/zzz", NOW - 900)
        self.scan(old, other)
        n = S.talk("n", "/w/new", NOW - 100, live=True)
        self.scan(old, other, n, dt=4)
        self.assertEqual(card_order(b), ["/w/new", "/w/old", "/w/zzz"])
        # idle first, then running: moves ahead only after 8 s
        b2 = S.make_backend(S.Clock(), settings_file())
        b2.apply_scan([old, other])
        b2.apply_scan([old, other, dict(n, live=False)])
        self.assertEqual(card_order(b2), ["/w/old", "/w/new", "/w/zzz"])
        for dt, want in ((4, "/w/old"), (4, "/w/old"), (4, "/w/new")):
            b2._test_clock.advance(dt)
            b2.apply_scan([old, other, n])
            self.assertEqual(card_order(b2)[0], want)
        b2.setFilter("reverse", True)
        self.assertEqual(card_order(b2), ["/w/zzz", "/w/old", "/w/new"])


class SelectionTest(unittest.TestCase):
    def setUp(self):
        self.b = backend()
        self.b.apply_scan([S.talk("a", "/w/x", NOW), S.talk("b", "/w/x/deep", NOW),
                           S.talk("c", "/w/y", NOW), S.talk("arch", "/w/x", NOW, archived=True),
                           S.talk("xx", "/w/xx", NOW)])

    def test_sel2_select_folder(self):
        b = self.b
        b.select("claude:c", False)
        b.selectFolder("/w/x")
        self.assertEqual(b.selection, {"claude:a", "claude:b"})
        self.assertEqual(b.details["key"], "claude:a")
        b.select("claude:b", False)
        b.selectFolder("/w/x")
        self.assertEqual(b.details["key"], "claude:b")
        b.selectFolder("/nowhere")
        self.assertEqual((b.selection, b.details), (set(), {}))

    def test_sel4_action_keys(self):
        b = self.b
        b.select("claude:c", False)
        b.select("claude:a", True)
        self.assertEqual(b.actionKeys("claude:c"), ["claude:a", "claude:c"])
        self.assertEqual(b.actionKeys("claude:b"), ["claude:b"])
        self.assertEqual(b.selectedCount, 2)
        b.select("claude:a", True)
        self.assertEqual((b.selection, b.details["key"]), ({"claude:c"}, "claude:c"))
        b.clearSelection()
        self.assertEqual(b.details, {})

    def test_details(self):
        b = self.b
        b.select("claude:a", False)
        d = b.details
        rows = dict(zip(d["rows"][::2], d["rows"][1::2]))
        self.assertEqual(rows["size"], "2.0 MB")
        self.assertEqual(rows["prompts"], "2")
        self.assertEqual(rows["context"], "1k tokens")
        self.assertEqual(rows["subagents"], "—")
        self.assertEqual(rows["file"], "/x/a")
        self.assertEqual(b.describe(json.dumps(["claude:a", "nope"]))[0]["key"], "claude:a")


class FoldTest(unittest.TestCase):
    def setUp(self):
        self.b = backend()
        self.talks = [S.talk("t%d" % i, "/w/a", NOW - i * 60) for i in range(8)] + [
            S.talk("b", "/w/b", NOW - 30), S.talk("deep", "/w/c/d/e", NOW - 40 * 86400),
            S.talk("deep2", "/w/c/f", NOW - 40 * 86400)]
        self.b.setFilter("age", 0)
        self.b.apply_scan(self.talks)

    def test_fold2_card_modes(self):
        b = self.b
        compact = geom(b, "card:/w/a")[3]
        b.setCardMode("/w/a", "collapsed")
        self.assertEqual(geom(b, "card:/w/a")[3], 30)
        b.setCardMode("/w/a", "full")
        self.assertGreater(geom(b, "card:/w/a")[3], compact)
        with open(b.settings_path) as f:
            self.assertEqual(json.load(f)["cards"], {"/w/a": "full"})

    def test_fold3_set_all_under(self):
        b = self.b
        b.setAllUnder("/", True)
        self.assertTrue(all(v == "collapsed" for v in b.cards.values()))
        self.assertIn("/w/c/d/e", b.cards)               # hidden under a folded folder
        self.assertTrue(b.dirs.get("/w/c"))
        self.assertNotIn("/", b.dirs)
        self.assertNotIn("/w", b.dirs)                    # the top node (a collapsed chain) stays open
        b.setAllUnder("/", False)
        self.assertTrue(all(v == "compact" for v in b.cards.values()))
        self.assertFalse(any(b.dirs.values()))
        b.setAllUnder("/w/c", True)
        self.assertNotIn(True, [b.dirs.get("/w/c")])
        self.assertEqual(b.cards["/w/c/d/e"], "collapsed")
        self.assertEqual(b.cards["/w/a"], "compact")

    def test_fold4_auto(self):
        b = self.b
        b.toggleDir("/w/c")
        b.setCardMode("/w/a", "full")
        b.autoFold()
        self.assertEqual((b.dirs, b.cards), ({}, {}))
        with open(b.settings_path) as f:
            data = json.load(f)
        self.assertEqual((data["dirs"], data["cards"]), ({}, {}))

    def test_fold5_card_fold_moves_nothing_above(self):
        for held in (False, True):
            b = backend()
            b.setFilter("age", 0)
            talks = [S.talk("k%d" % i, "/w/c%d" % i, NOW - i * 60) for i in range(9)]
            b.apply_scan(talks)
            b.holdLayout(held)
            ranks = dict(b.ranks.as_dict())
            before = {n["uid"]: (n["nx"], n["ny"]) for n in b._nodes.items() if n["kind"] == "card"}
            target = "card:/w/c3"
            b.setCardMode("/w/c3", "collapsed")
            after = {n["uid"]: (n["nx"], n["ny"]) for n in b._nodes.items() if n["kind"] == "card"}
            self.assertEqual(after[target], before[target])
            for uid, (x, y) in before.items():
                if y <= before[target][1]:
                    self.assertEqual(after[uid], (x, y), uid)
            self.assertEqual(b.ranks.as_dict(), ranks)

    def test_fold5_top_folder_toggles(self):
        b = self.b
        b.toggleDir("/w")
        self.assertEqual([n["uid"] for n in b._nodes.items()], ["dir:/w"])
        b.toggleDir("/w")
        self.assertGreater(len(b._nodes), 3)


class DropTest(unittest.TestCase):
    def test_drop_highlight(self):
        b = backend()
        b.apply_scan([S.talk("a", "/w/x", NOW), S.talk("b", "/w/y", NOW)])
        x = next(n for n in b._nodes.items() if n["uid"] == "card:/w/x")
        self.assertEqual(b.dropAt(x["nx"] + 5, x["ny"] + 5), "/w/x")
        self.assertTrue(next(n for n in b._nodes.items() if n["uid"] == "card:/w/x")["drop"])
        self.assertEqual(b.dropAt(-1e9, -1e9), "")
        self.assertFalse(any(n["drop"] for n in b._nodes.items()))


class AgentToggleTest(unittest.TestCase):
    def test_srch10_and_pan19(self):
        b = backend()
        b._askable = {"claude", "codex"}
        b.apply_scan([S.talk("a", "/p", NOW), S.talk("c", "/p", NOW, tool="codex")])
        rec = Recorder(b)
        usage = {"claude": {"bars": [{"label": "week", "used": 10}], "observed_at": NOW},
                 "codex": {"bars": [{"label": "week", "used": 20}], "observed_at": NOW}, "at": NOW}
        b.apply_usage(usage)
        self.assertEqual(rec["usageChanged"], 1)
        b.apply_usage(dict(usage))
        self.assertEqual(rec["usageChanged"], 1)
        self.assertEqual(b.searchAgent, {"name": "claude", "label": "Claude"})
        b.ai_answer(b._ai_gen, [])  # ignored: wrong state is fine, results empty
        b._ai = dict(b._ai_idle(), results=[{"key": "codex:c", "tool": "codex", "why": "", "title": "c", "folder": "/p"},
                                           {"key": "claude:a", "tool": "claude", "why": "", "title": "a", "folder": "/p"}])
        b._ai_keys = {"codex:c", "claude:a"}
        rec.reset()
        b.setFilter("codex", False)
        self.assertEqual([g["key"] for g in b.usage], ["claude"])
        self.assertEqual(rec["usageChanged"], 1)
        self.assertEqual(rec["aiChanged"], 1)
        self.assertEqual([r["key"] for r in b.ai["results"]], ["claude:a"])
        self.assertEqual([t["name"] for t in b.activity["tools"]], ["claude"])
        self.assertEqual(b.searchAgent["name"], "claude")
        rec.reset()
        b.apply_usage(dict(usage, codex={"bars": [{"label": "week", "used": 90}], "observed_at": NOW}))
        self.assertEqual(rec["usageChanged"], 0)
        b.setFilter("codex", True)
        self.assertEqual(rec["usageChanged"], 1)
        self.assertEqual(b.usage[1]["bars"][0]["left"], 10)
        rec.reset()
        b.setFilter("gemini", False)
        self.assertEqual((rec["usageChanged"], rec["aiChanged"]), (0, 0))


class NewTalkTest(unittest.TestCase):
    def test_opens_leftmost_agent_in_folder(self):
        b = backend()
        b.apply_scan([S.talk("a", "/p", NOW), S.talk("c", "/p", NOW, tool="codex")])
        rec = Recorder(b)
        with mock.patch.object(b, "_spawn", side_effect=lambda fn, done, *a: done(fn(*a))), \
                mock.patch.object(ops, "run_in_terminal") as run:
            b.newTalk("/p")
            b.reorderAgents(["codex", "claude"])
            self.assertEqual(b.talkAgent, {"name": "codex", "label": "Codex"})
            b.newTalk("/q")
        self.assertEqual(run.call_args_list, [mock.call("/p", "claude"), mock.call("/q", "codex")])
        self.assertEqual(rec.toasts, [("opening claude…", False), ("opening codex…", False)])

    def test_skips_agents_without_a_new_command(self):
        b = backend()
        b.apply_scan([S.talk("a", "/p", NOW)])
        from atree.agents import BY_NAME
        with mock.patch.object(BY_NAME["claude"], "new_command", None), \
                mock.patch.object(b, "_spawn") as spawn:
            self.assertEqual(b.talkAgent, {})
            b.newTalk("/p")
        spawn.assert_not_called()


class AgentOrderTest(unittest.TestCase):
    def test_order_drives_chips_usage_and_search_and_persists(self):
        b = backend()
        b._askable = {"claude", "codex"}
        b.apply_scan([S.talk("a", "/p", NOW), S.talk("c", "/p", NOW, tool="codex"),
                      S.talk("g", "/p", NOW, tool="gemini")])
        b.apply_usage({"claude": {"bars": [], "observed_at": NOW}, "codex": {"bars": [], "observed_at": NOW}})
        rec = Recorder(b)
        b.reorderAgents(["gemini", "codex", "claude"])
        self.assertEqual([a["name"] for a in b.agents], ["gemini", "codex", "claude"])
        self.assertEqual([g["key"] for g in b.usage], ["codex", "claude"])
        self.assertEqual(b.searchAgent["name"], "codex")  # gemini can't search here
        self.assertEqual([t["name"] for t in b.activity["tools"]], ["gemini", "codex", "claude"])
        self.assertEqual((rec["changed"], rec["usageChanged"]), (1, 1))
        # usage row reorders only its own agents, among their slots
        b.reorderAgents(["claude", "codex"])
        self.assertEqual([a["name"] for a in b.agents], ["gemini", "claude", "codex"])
        rec.reset()
        b.reorderAgents(["claude", "codex"])  # no change, no signals
        self.assertEqual((rec["changed"], rec["usageChanged"]), (0, 0))
        with open(b.settings_path) as f:
            saved = json.load(f)["order"]
        self.assertEqual(saved[:3], ["gemini", "claude", "codex"])
        b2 = S.make_backend(settings_path=b.settings_path)
        self.assertEqual(b2._order, saved)


class AiTest(unittest.TestCase):
    def test_answer_and_clear(self):
        b = backend()
        b.apply_scan([S.talk("a", "/p", NOW, title="  two\n words "), S.talk("b", "/q", NOW - 400 * 86400)])
        Recorder(b)
        with mock.patch("atree.aisearch.search", return_value=[]), mock.patch.object(b, "_spawn"):
            b.aiSearch("claude", "idea")
            self.assertTrue(b.ai["busy"])
            b.aiSearch("claude", "again")        # ignored while busy
            self.assertEqual(b.ai["query"], "idea")
            b.aiSearch("nobody", "x")
        gen = b._ai_gen
        b.ai_answer(gen, [{"key": "claude:b", "why": "old"}, {"key": "claude:zz", "why": "gone"}])
        self.assertEqual(b.ai["results"], [{"key": "claude:b", "why": "old", "title": "b",
                                            "folder": "/q", "tool": "claude"}])
        self.assertEqual(b.stats.split(" ")[0], "1")
        b.clearAi()
        self.assertEqual(b.ai["results"], [])
        self.assertEqual(b.stats.split(" ")[0], "1")   # only "a" within 30 days
        b._ai = dict(b._ai, busy=True)
        b._ai_gen += 1
        late = b._ai_gen
        b.clearAi()
        b.ai_answer(late, [{"key": "claude:a", "why": ""}])
        self.assertEqual(b.ai["results"], [])
        b.ai_answer(b._ai_gen, [{"key": "claude:zz"}])
        self.assertEqual(b.ai["error"], "nothing fits")
        b.ai_answer(b._ai_gen, RuntimeError("Claude failed: x"))
        self.assertEqual(b.ai["error"], "Claude failed: x")


class BatchTest(unittest.TestCase):
    def setUp(self):
        self.b = backend()
        self.b.collector = lambda: ([], [])
        self.b.apply_scan([S.talk("one", "/p", NOW), S.talk("two", "/p", NOW)])
        self.b.select("claude:one", False)
        self.rec = Recorder(self.b)
        self.refreshes = []
        self.b.refresh = lambda: self.refreshes.append(1)

    def test_batch1_partial(self):
        self.b.batch_done((["claude:one"], ["e1", "e2"]), "archived %d talk(s)", True)
        self.assertEqual(self.rec.undos, [("archived 1 talk(s)\ne1\ne2", ["claude:one"], True)])
        self.assertEqual(self.rec.toasts, [])
        self.common()

    def test_batch2_full(self):
        self.b.batch_done((["claude:one", "claude:two"], []), "archived %d talk(s)", True)
        self.assertEqual(self.rec.undos, [("archived 2 talk(s)", ["claude:one", "claude:two"], False)])
        self.assertEqual(self.rec.toasts, [])
        self.common()

    def test_batch3_no_success(self):
        self.b.batch_done(([], ["e1", "e2"]), "archived %d talk(s)", True)
        self.assertEqual((self.rec.undos, self.rec.toasts), ([], [("e1\ne2", True)]))
        self.common()
        self.rec.reset()
        self.b.batch_done(RuntimeError("worker died"), "archived %d talk(s)", True)
        self.assertEqual(self.rec.toasts, [("worker died", True)])

    def test_batch4_plain(self):
        self.b.batch_done((["claude:one"], ["bad"]), "moved %d talk(s)")
        self.assertEqual(self.rec.toasts, [("bad", True)])
        self.rec.reset()
        self.b.batch_done((["claude:one"], []), "restored %d talk(s)")
        self.assertEqual(self.rec.toasts, [("restored 1 talk(s)", False)])

    def common(self):
        self.assertEqual(self.b.selection, set())
        self.assertEqual(len(self.refreshes), 1)

    def test_archive_filters_and_runs_ops(self):
        b = backend()
        rec = Recorder(b)
        b.apply_scan([S.talk("r", "/p", NOW, live=True), S.talk("ok", "/p", NOW)])
        b.archive(["claude:r"])
        self.assertEqual(rec.toasts, [("running talks can't be archived", True)])
        b.deleteArchived(["claude:ok"])
        self.assertEqual(rec.toasts[-1], ("archive a talk before deleting it", True))
        done = []
        with mock.patch.object(ops, "archive", side_effect=lambda k: done.append(k)):
            b.collector = lambda: ([], [])
            b.archive(["claude:r", "claude:ok"])
            for _ in range(20):
                S.pump(20)
                if rec.undos:
                    break
        self.assertEqual(done, ["claude:ok"])
        self.assertEqual(rec.undos, [("archived 1 talk(s)", ["claude:ok"], False)])
        b.shutdown()


class TasksTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.patch = mock.patch.object(ops, "TASKS_FILE", os.path.join(self.dir, "tasks.json"))
        self.patch.start()
        self.b = backend()
        self.rec = Recorder(self.b)

    def tearDown(self):
        self.patch.stop()

    def disk(self):
        with open(ops.TASKS_FILE) as f:
            return json.load(f)

    def test_task6_and_8(self):
        b, rec = self.b, self.rec
        b.addTask("  buy\n milk ")
        b.addTask("   ")
        self.assertEqual([t["text"] for t in b.tasks], ["buy milk"])
        self.assertEqual(rec["tasksChanged"], 1)
        tid = b.tasks[0]["id"]
        b.setTaskDone(tid, False)
        b.editTask(tid, "  ")
        self.assertEqual(rec["tasksChanged"], 1)
        b.addTask("second")
        b.addTask("third")
        ids = [t["id"] for t in b.tasks]
        b.moveTask(ids[2], 0)
        self.assertEqual([t["text"] for t in b.tasks], ["third", "buy milk", "second"])
        b.moveTask(ids[2], 3)
        self.assertEqual([t["text"] for t in b.tasks], ["buy milk", "second", "third"])
        b.setTaskDone(ids[0], True)
        b.clearDoneTasks()
        self.assertEqual([t["text"] for t in self.disk()], ["second", "third"])

    def test_task8_save_failure(self):
        b, rec = self.b, self.rec
        b.addTask("keep")
        rec.reset()
        with mock.patch.object(tasks_mod, "atomic_write", side_effect=OSError(28, "No space left on device")):
            b.addTask("lost")
        self.assertEqual(rec.toasts, [("can't save tasks: [Errno 28] No space left on device", True)])
        self.assertEqual([t["text"] for t in b.tasks], ["keep"])
        self.assertEqual([t["text"] for t in self.disk()], ["keep"])
        self.assertEqual(rec["tasksChanged"], 0)

    def test_task8_external_change(self):
        b, rec = self.b, self.rec
        b.addTask("mine")
        rec.reset()
        st = os.stat(ops.TASKS_FILE)
        with open(ops.TASKS_FILE, "w") as f:
            json.dump([{"id": "x1", "text": "theirs", "done": False}, "junk", {"text": "no id"}], f)
        os.utime(ops.TASKS_FILE, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        b.setTaskDone("x1", False)        # no-op edit still reports the outside change
        self.assertEqual(rec["tasksChanged"], 1)
        self.assertEqual([t["text"] for t in b.tasks], ["theirs"])
        with open(ops.TASKS_FILE, "w") as f:
            json.dump({"not": "a list"}, f)
        os.utime(ops.TASKS_FILE, ns=(st.st_atime_ns, st.st_mtime_ns + 2 * 10 ** 9))
        with mock.patch.object(tasks_mod, "atomic_write", side_effect=OSError(28, "No space left on device")):
            b.addTask("x")
        self.assertEqual(rec["tasksChanged"], 2)
        self.assertEqual(b.tasks, [])


class SettingsBackendTest(unittest.TestCase):
    def test_persist_and_reload(self):
        path = settings_file()
        b = S.make_backend(settings_path=path)
        b.setFilter("sort", "name")
        b.setFilter("sort", "bogus")
        b.setFilter("age", "7")
        b.setFilter("q", "query")
        b.toggleDir("/nowhere")
        b2 = S.make_backend(settings_path=path)
        self.assertEqual((b2.filters["sort"], b2.filters["age"], b2.filters["q"]), ("name", 7, ""))
        self.assertEqual(b2.dirs, {"/nowhere": True})

    def test_wid2_settings_are_read_only_at_start(self):
        path = settings_file()
        app = S.make_backend(settings_path=path)
        widget = S.make_backend(settings_path=path)
        widget.apply_scan([S.talk("a", "/w/x", NOW), S.talk("b", "/w/y", NOW)])
        rec = Recorder(widget)
        app.setFilter("sort", "name")
        app.setCardMode("/w/x", "full")
        st = os.stat(path)
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10 ** 9))
        with mock.patch.object(widget, "refresh"):
            widget._tick()
        self.assertEqual((widget.filters["sort"], widget.cards), ("recent", {}))
        self.assertEqual(rec["changed"], 0)
        # last writer wins
        widget.setFilter("live", True)
        with open(path) as f:
            saved = json.load(f)
        self.assertEqual((saved["filters"]["sort"], saved["cards"]), ("recent", {}))
        self.assertEqual(S.make_backend(settings_path=path).filters["live"], True)

    def test_save_failure_toasts(self):
        b = S.make_backend(settings_path="/proc/atree-cannot-write/ui.json")
        rec = Recorder(b)
        b.setFilter("live", True)
        self.assertTrue(rec.toasts and rec.toasts[0][0].startswith("can't save settings: [Errno"))


class PathTest(unittest.TestCase):
    def test_dlg2_url_round_trip(self):
        b = backend()
        tmp = tempfile.mkdtemp()
        for p in ["/", tmp, tmp + "/with space", tmp + "/a#b", tmp + "/q?x=1", tmp + "/lit%20pct",
                  tmp + "/100%", tmp + "/日本語 ü", "~/a#b"]:
            url = b.localFileUrl(p)
            self.assertEqual(url.scheme(), "file", p)
            self.assertFalse(url.hasFragment(), p)
            self.assertFalse(url.hasQuery(), p)
            self.assertEqual(b.localFilePath(url), b.expand(p), p)
            self.assertEqual(b.localFilePath(url.toString()), b.expand(p), p)
        self.assertEqual(b.expand("  ~/x "), os.path.join(S.HOME, "x"))
        self.assertTrue(b.isDir(" ~ "))
        self.assertFalse(b.isDir(""))
        self.assertEqual(b.tildify(S.HOME + "/x"), "~/x")


class IdleRescanTest(unittest.TestCase):
    def test_identical_rescan_skips_rebuild_until_the_minute_changes(self):
        b = backend()
        talks = [S.talk("a", "/p", NOW - 100), S.talk("b", "/q", NOW - 5000)]
        b.apply_scan(talks)
        with mock.patch("atree.qt.layout.compute_layout", wraps=L.compute_layout) as layout:
            b.apply_scan([dict(t) for t in talks])
            self.assertEqual(layout.call_count, 0)
            b.clock = lambda: NOW + 120
            b.apply_scan([dict(t) for t in talks])
            self.assertEqual(layout.call_count, 1)
            b.apply_scan([dict(talks[0], title="renamed"), talks[1]])
            self.assertEqual(layout.call_count, 2)


if __name__ == "__main__":
    unittest.main()
