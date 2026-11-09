"""Visual and interaction details matched to the original (spec/gaps-ui.md G-ui-1 … G-ui-21)."""
import unittest

import ui_support as S
from ui_support import Qt

from PySide6.QtTest import QTest

from atree.qt import layout as L

NOW = S.NOW
DAY = 86400
AGENTS = ["claude", "codex", "gemini", "opencode", "copilot"]
USAGE = {
    "claude": {"observed_at": NOW, "bars": [
        {"label": "session (5h)", "used": 31, "window": 18000, "resets_at": NOW + 3600},
        {"label": "week", "used": 17, "window": 604800, "resets_at": NOW + 9 * 3600}]},
    "codex": {"observed_at": NOW, "bars": [
        {"label": "week", "used": 0, "window": 604800, "resets_at": NOW - 5, "stale": True}]},
    "at": NOW}


def child(item, name):
    return next(it for it in S.walk(item) if it.objectName() == name)


def children(item, name):
    return [it for it in S.walk(item) if it.objectName() == name]


def item_for(v, name, path):
    return next(it for it in v.find_all(name) if it.property("path") == path)


def srect(item):
    """Scene rectangle in world units (the tree is drawn scaled)."""
    from PySide6.QtCore import QRectF
    return item.mapRectToScene(QRectF(0, 0, item.width(), item.height()))


def ctx(**kw):
    filters = L.default_filters(AGENTS)
    filters.update(kw.pop("filters", {}))
    return L.Ctx(filters, NOW, kw.pop("home", "/home/u"), **kw)


THEME = {"bg": "#0f1217", "text": "#e6e9f0", "dim": "#5f6779", "accent": "#b48cff"}


def theme(v, name):
    from PySide6.QtGui import QColor
    return QColor(THEME[name])


# ---------------------------------------------------------------- window-free logic

class LogicTest(unittest.TestCase):
    def test_g14_name_sort_ranks_a_chain_by_its_first_segment(self):
        talks = [S.talk("k", "/h/Downloads/vendor/M100 user manual", NOW),
                 S.talk("e", "/h/egg", NOW), S.talk("z", "/h/zoo", NOW), S.talk("x", "/h/x", NOW)]
        c = ctx(filters={"sort": "name", "age": 0}, home="/nowhere")
        lay = L.compute_layout(L.visible_talks(talks, c.filters, NOW), c)
        top = next(n for n in lay.nodes if n["uid"] == "dir:/h")
        cards = [n for n in lay.nodes if n["kind"] == "card"]
        order = [n["path"] for n in sorted(cards, key=lambda n: (n["ny"], n["nx"]))]
        self.assertTrue(top)
        # "downloads" < "egg" < "x" < "zoo"; by the last segment "m100…" would come after "egg"
        self.assertEqual(order, ["/h/Downloads/vendor/M100 user manual", "/h/egg", "/h/x", "/h/zoo"])

    def test_g2_percent_left_rounds_the_float_half_up(self):
        bar = lambda used: L.usage_bar({"used": used}, NOW)
        self.assertEqual((bar(13.5)["left"], bar(13.5)["used"]), (87, 14))
        self.assertEqual((bar(12.5)["left"], bar(12.5)["used"]), (88, 13))
        self.assertEqual((bar(12.4)["left"], bar(12.4)["used"]), (88, 12))
        self.assertEqual((bar(130)["left"], bar(130)["used"]), (0, 100))
        self.assertEqual((bar(0.4)["left"], bar(0.4)["used"]), (100, 0))

    def test_g21_compact_rule_is_strict(self):
        items = [(S.talk("a", "/p", NOW - L.COMPACT_WITHIN), 0)]
        self.assertEqual(L.card_mode("/p", items, ctx()), "collapsed")
        items = [(S.talk("a", "/p", NOW - L.COMPACT_WITHIN + 1), 0)]
        self.assertEqual(L.card_mode("/p", items, ctx()), "compact")

    def test_g21_untitled_talk_has_an_empty_title(self):
        t = S.talk("a", "/p", NOW, title="")
        self.assertEqual(L.row_data(t, 0, ctx())["title"], "")

    def test_g6_all_tree_lists_the_top_node_first(self):
        talks = [S.talk("a", "/home/u/a", NOW), S.talk("b", "/home/u/b/c", NOW)]
        dirs, cards = L.all_tree(talks, ctx())
        self.assertEqual(dirs[0], "/home/u")


class FoldAllTest(unittest.TestCase):
    def test_g6_fold_all_keeps_the_top_chain_open(self):
        b = S.make_backend()
        b.setFilter("age", 0)
        home = S.HOME
        talks = [S.talk("a", home + "/a", NOW - 60), S.talk("b", home + "/b/x", NOW - 40 * DAY),
                 S.talk("c", home + "/b/y", NOW - 40 * DAY)]
        b.apply_scan(talks)
        b.setAllUnder("/", True)
        self.assertNotIn(home, b.dirs)
        self.assertTrue(b.dirs[home + "/b"])
        top = next(n for n in b.shown.nodes if n["uid"] == "dir:" + home)
        self.assertFalse(top["collapsed"])
        self.assertEqual(top["label"], "~")
        b.shutdown()


class UsageRefreshTest(unittest.TestCase):
    def test_g21_timer_does_not_force_explicit_refresh_does(self):
        b = S.make_backend()
        seen = []
        b.usage_source = lambda force=False: seen.append(force) or {}
        b._usage_timer.timeout.emit()
        b.refreshUsage()
        S.pump(100)
        self.assertEqual(sorted(seen), [False, True])
        b.shutdown()


class HeaderTest(unittest.TestCase):
    def make(self, width):
        b = S.make_backend()
        b.apply_scan([S.talk("a", "/w/a", NOW)])
        b.apply_usage(USAGE)
        v = S.View(b, size=(width, 950))
        S.pump(100)
        return b, v

    def test_g1_full_groups_move_to_a_second_band_instead_of_clipping(self):
        b, v = self.make(1500)
        header = v.find("header")
        self.assertTrue(header.property("twoRows"))
        groups = sorted(v.find_all("usageGroup"), key=lambda g: S.scene_rect(g).x())
        self.assertEqual(len(groups), 2)
        for g in groups:
            for bar in children(g, "usageBar"):
                parts = [c for c in bar.childItems() if c.isVisible()]
                self.assertEqual(len(parts), 4)                    # label, track, % left, reset
                self.assertEqual(parts[0].width(), 74)
                self.assertEqual(parts[1].width(), 110)
        for item in groups + [v.find("headerTools")]:
            rect = S.scene_rect(item)
            self.assertLessEqual(rect.right(), 1500 - 14)
            self.assertGreaterEqual(rect.y(), 66)
            self.assertLessEqual(rect.bottom(), header.height())
        self.assertEqual(S.scene_rect(v.find("headerTools")).x(), 14)
        v.close()

    def test_header_reflows_with_the_width_and_never_clips(self):
        for width in (900, 1100, 1300):
            b, v = self.make(width)
            header = v.find("header")
            self.assertTrue(header.property("twoRows"))
            for item in v.find_all("usageGroup") + [v.find("headerTools")]:
                self.assertLessEqual(S.scene_rect(item).right(), width - 14, width)
                self.assertLessEqual(S.scene_rect(item).bottom(), header.height(), width)
            self.assertAlmostEqual(S.scene_rect(v.find("treeCanvas")).y(), header.height(), delta=0.5)
            self.assertEqual(v.all_warnings(), [])
            v.close()
        b, v = self.make(2400)
        header = v.find("header")
        self.assertFalse(header.property("twoRows"))
        self.assertEqual(header.height(), 66)
        tools = S.scene_rect(v.find("headerTools"))
        self.assertLess(tools.bottom(), 66)
        v.close()

    def test_g1_wide_window_right_aligns_all_groups(self):
        b, v = self.make(2400)
        groups = sorted(v.find_all("usageGroup"), key=lambda g: S.scene_rect(g).x())
        self.assertAlmostEqual(S.scene_rect(v.find("usageRow")).right(), 2400 - 14, delta=1)
        self.assertLess(S.scene_rect(groups[1]).right(), 2400)
        self.assertEqual(v.find("searchField").width(), 320)
        v.close()

    def test_1920_keeps_one_band_tools_on_chip_row_usage_centred(self):
        b = S.make_backend()
        tools = ["claude", "codex", "gemini", "opencode", "copilot"]
        b.apply_scan([S.talk("a" + t, "/w/a", NOW, tool=t) for t in tools])
        b.apply_usage(USAGE)
        v = S.View(b, size=(1920, 950))
        S.pump(100)
        header = v.find("header")
        self.assertFalse(header.property("twoRows"))
        self.assertEqual(header.height(), 66)
        toolbar = S.scene_rect(v.find("headerToolbar"))
        tool_rect = S.scene_rect(v.find("headerTools"))
        self.assertLess(tool_rect.bottom(), toolbar.y())                 # Fit… sit on the chip row
        row = S.scene_rect(v.find("usageRow"))
        self.assertAlmostEqual(row.center().y(), 33, delta=1)
        self.assertGreater(row.x(), max(tool_rect.right(), toolbar.right()))
        self.assertAlmostEqual(row.right(), 1920 - 14, delta=1)
        groups = v.find_all("usageGroup")
        self.assertEqual(sorted(len(children(g, "usageBar")) for g in groups), [1, 2])
        self.assertEqual(v.all_warnings(), [])
        v.close()

    def test_g2_bar_rows_are_3px_apart(self):
        b, v = self.make(2400)
        claude = next(g for g in v.find_all("usageGroup") if len(children(g, "usageBar")) == 2)
        r1, r2 = [S.scene_rect(r) for r in children(claude, "usageBar")]
        top, bottom = sorted((r1, r2), key=lambda r: r.y())
        self.assertAlmostEqual(bottom.y() - top.bottom(), 3, delta=0.5)
        v.close()

    def test_g9_search_box_and_control_rows(self):
        b, v = self.make(1500)
        h = v.find("searchField").height()
        self.assertTrue(26 <= h <= 30, h)
        rows = [r for r in v.find("searchField").parentItem().childItems()
                if r.metaObject().className().startswith("QQuickColumn")]
        spacings = {row.property("spacing") for col in rows for row in col.childItems()}
        self.assertEqual(spacings, {5})
        v.close()


class TreeDrawTest(unittest.TestCase):
    def setUp(self):
        self.b = S.make_backend()
        self.b.setFilter("age", 0)
        talks = [S.talk("r%d" % i, "/w/a", NOW - i * 60) for i in range(12)]
        talks += [S.talk("live", "/w/b", NOW, live=True), S.talk("c", "/w/c", NOW - 3 * DAY)]
        self.b.apply_scan(talks)
        self.v = S.View(self.b)
        S.pump(400)

    def tearDown(self):
        self.v.close()

    def test_g4_open_pill_is_exactly_its_layout_width(self):
        pill = item_for(self.v, "folderPill", "/w")
        self.assertFalse(S.qml_eval(pill, "node.collapsed"))
        self.assertAlmostEqual(pill.width(), S.qml_eval(pill, "node.nw"), delta=0.5)
        content = child(pill, "pillContent")
        self.assertEqual(content.property("spacing"), 7)
        chevron = content.childItems()[0]
        self.assertLess(chevron.width(), 12)                    # natural glyph width

    def test_g5_card_header_metrics(self):
        card = item_for(self.v, "card", "/w/b")
        head = child(card, "cardHeadRow")
        self.assertEqual(head.property("spacing"), 8)
        age = child(card, "cardAge")
        right = child(card, "cardRight")
        scale = self.v.find("world").property("scale")
        self.assertAlmostEqual((srect(card).right() - srect(right).right()) / scale, 8, delta=0.5)
        self.assertEqual(right.property("spacing"), 4)
        self.assertTrue(age.isVisible())
        # the running group is its own row: dot, 5 px, "N running"
        running = [r for r in head.childItems() if r.metaObject().className().startswith("QQuickRow")]
        self.assertEqual(len(running), 1)
        self.assertEqual(running[0].property("spacing"), 5)

    def test_g16_rows_clip_away_during_a_fold(self):
        card = item_for(self.v, "card", "/w/c")
        rows = child(card, "rowList")
        self.assertTrue(rows.isVisible())
        self.b.setCardMode("/w/c", "collapsed")
        S.pump(30)
        self.assertTrue(rows.isVisible())                       # still there while the height animates
        S.pump(400)
        self.assertFalse(rows.isVisible())

    def test_g16_card_scrollbar_and_wheel_hand_off(self):
        b = S.make_backend()
        b.setFilter("age", 0)
        b.apply_scan([S.talk("r%s_%d" % (f, i), "/w/%s" % f, NOW - i * 60) for f in "aefg" for i in range(12)])
        v = S.View(b, size=(1500, 500))
        S.pump(300)
        S.call(v.find("treeCanvas"), "resetZoom")
        S.pump(400)
        card = item_for(v, "card", "/w/a")
        rows = child(card, "rowList")
        bar = child(card, "cardScrollBar")
        self.assertTrue(bar.isVisible())
        self.assertEqual(bar.width(), 8)
        self.assertEqual(bar.property("opacity"), 0.5)
        end = rows.property("contentHeight") - rows.height()
        rows.setProperty("contentY", end - 10)
        world = v.find("world")
        before = world.property("y")
        p = srect(rows).center()
        S.wheel(v.win, p.x(), p.y(), dy=-120)
        S.pump(400)
        self.assertAlmostEqual(rows.property("contentY"), end, delta=0.5)
        self.assertLess(world.property("y"), before)            # the rest of the step panned the canvas
        v.close()

    def test_g21_row_buttons_and_italic_archived_match(self):
        self.b.setFilter("archived", True)
        self.b.apply_scan(self.b.talks + [S.talk("old", "/w/c", NOW - DAY, archived=True, title="needle")])
        self.b.setFilter("q", "needle")
        S.pump(400)
        row = next(r for r in self.v.find_all("talkRow") if r.property("key") == "claude:old")
        title = next(t for t in S.walk(row) if t.property("text") == "needle")
        self.assertTrue(title.property("font").italic())


class PanelsTest(unittest.TestCase):
    def make(self, **kw):
        b = S.make_backend()
        b.apply_scan(kw.pop("talks", [S.talk("a", "/w/a", NOW, days={})]))
        v = S.View(b, **kw)
        S.pump(300)
        return b, v

    def test_g7_month_totals_side_by_side(self):
        b, v = self.make()
        totals = v.find_all("activityTotals")
        self.assertTrue(totals)
        row = totals[0]
        self.assertEqual((row.property("spacing"), row.property("topPadding")), (10, 6))
        a, c = [x for x in row.childItems() if x.metaObject().className().startswith("QQuickText")]
        self.assertAlmostEqual(c.x() - a.x() - a.width(), 10, delta=0.5)
        v.close()

    def test_g8_calendar_empty_line(self):
        b, v = self.make()
        panel = v.find("calendarPanel")
        empty = child(panel, "calendarEmpty")
        self.assertEqual(empty.property("color"), theme(v, "dim"))
        self.assertAlmostEqual(S.scene_rect(empty).x() - S.scene_rect(panel).x(), 14, delta=0.5)
        v.close()

    def test_g11_dialog_lists_all_talks_and_uses_light_ok(self):
        talks = [S.talk("m%d" % i, "/w/a", NOW - i) for i in range(9)]
        b, v = self.make(talks=talks)
        S.call(v.win, "openMoveDialog", [t["key"] for t in talks], S.HOME)
        S.pump(200)
        dlg = v.find("moveDialog")
        lst = child(dlg.property("contentItem"), "dialogTalks")
        self.assertEqual((lst.property("count"), lst.height()), (9, 132))
        self.assertGreater(lst.property("contentHeight"), lst.height())
        ok = child(dlg.property("contentItem"), "dialogOk")
        label = next(c for c in ok.childItems() if c.property("text") == "Move")
        self.assertFalse(label.property("font").bold())
        self.assertEqual(label.property("color"), theme(v, "text"))
        self.assertEqual(ok.property("color"), theme(v, "accent"))
        S.call(dlg, "close")
        S.pump(200)
        v.close()

    def test_g12_no_prompt_hides_the_prompt_block(self):
        b, v = self.make(talks=[S.talk("a", "/w/a", NOW, first_msg="")])
        b.select("claude:a", False)
        S.pump(300)
        details = v.find("detailsPanel")
        self.assertFalse(child(details, "promptWell").isVisible())
        self.assertFalse(any(t.property("text") == "First prompt" and t.isVisible() for t in S.walk(details)))
        b.apply_scan([S.talk("a", "/w/a", NOW, first_msg="hello")])
        S.pump(100)
        self.assertTrue(child(details, "promptWell").isVisible())
        v.close()

    def test_g13_task_rows_are_top_aligned(self):
        b = S.make_backend()
        b.apply_scan([S.talk("a", "/w/a", NOW)])
        b.addTask("a long task " * 12)
        v = S.View(b)
        S.pump(300)
        box = v.find_all("taskBox")[0]
        text = v.find_all("taskText")[0]
        self.assertEqual((box.x(), box.y()), (18, 6))
        self.assertEqual((text.x(), text.y()), (40, 5))
        self.assertEqual(text.width(), text.parentItem().width() - 40 - 26)
        tick = box.childItems()[0]
        self.assertEqual(tick.property("color"), theme(v, "bg"))
        list_ = v.find("taskList")
        self.assertEqual(list_.y(), 34)
        v.close()

    def test_g18_sidebar_flicks_and_shows_a_bar_only_on_overflow(self):
        b, v = self.make(size=(1500, 950))
        bar = v.find("sidebar")
        self.assertFalse(bar.property("interactive"))
        self.assertFalse(v.find("sidebarScrollBar").isVisible())
        v.close()
        b = S.make_backend()
        b.apply_scan([S.talk("a", "/w/a", NOW)])
        for i in range(30):
            b.addTask("task %d" % i)
        v = S.View(b, size=(1500, 520))
        S.pump(300)
        bar = v.find("sidebar")
        self.assertTrue(bar.property("interactive"))
        self.assertTrue(v.find("sidebarScrollBar").isVisible())
        v.close()

    def test_g19_toast_metrics(self):
        b, v = self.make()
        toast = v.find("toast")
        S.call(toast, "show", "archived 1 talk(s)", False, ["claude:a"])
        S.pump(50)
        msg = child(toast, "toastText")
        undo = child(toast, "toastUndo")
        line = msg.parentItem()
        self.assertAlmostEqual(toast.width(), line.width() + 28, delta=0.5)
        self.assertAlmostEqual(toast.height(), line.height() + 16, delta=0.5)
        self.assertAlmostEqual(undo.x() - msg.x() - msg.width(), 14, delta=0.5)
        S.call(toast, "show", "word " * 400, True, [])
        S.pump(50)
        self.assertAlmostEqual(toast.width(), 1500 * 0.7, delta=1)
        v.close()

    def test_g15_tooltip_is_the_stock_look(self):
        b, v = self.make()
        tip = v.find("sharedTip")
        self.assertEqual(tip.property("background").property("radius"), 0)
        owner = v.find("activityPanel")
        S.call(tip, "ask", owner, "x" * 200, 1)
        S.pump(100)
        self.assertGreater(tip.property("width"), 440)          # no width cap
        S.call(tip, "drop", owner)
        v.close()


class InteractionTest(unittest.TestCase):
    def test_menu_long_header_elides_inside_the_window(self):
        b = S.make_backend()
        b.apply_scan([S.talk("a", "/w/a", NOW)])
        v = S.View(b)
        self.addCleanup(v.close)
        title = "/a very long folder name" * 200
        for width in (1500, 360, 220):
            v.win.resize(width, 950)
            S.pump(50)
            S.call(v.win, "openFolderMenu", title, v.win.contentItem(), width - 5, 945)
            S.pump(150)
            menu = v.find("folderMenu")
            header = next(t for t in S.walk(menu.property("contentItem"))
                          if t.property("text") == title)
            self.assertLessEqual(menu.property("width"), min(480, width - 16))
            self.assertGreaterEqual(menu.property("x"), 0)
            self.assertLessEqual(menu.property("x") + menu.property("width"), width)
            self.assertLessEqual(header.width(), menu.property("width"))
            self.assertTrue(header.property("truncated"))
            for entry in children(menu.property("contentItem"), "menuEntry"):
                self.assertLessEqual(entry.width(), menu.property("width"))
            QTest.keyClick(v.win, Qt.Key_Escape)
            S.pump(150)
        self.assertEqual(v.all_warnings(), [])

    def test_g17_menu_keyboard_navigation_and_flip(self):
        b = S.make_backend()
        b.setFilter("age", 0)
        b.apply_scan([S.talk("a", "/w/a/x", NOW), S.talk("b", "/w/b", NOW)])
        v = S.View(b)
        S.pump(300)
        pill = item_for(v, "folderPill", "/w")
        S.call(v.win, "openFolderMenu", "/w", pill, 0, 0)
        S.pump(150)
        menu = v.find("folderMenu")
        self.assertTrue(menu.property("opened"))
        QTest.keyClick(v.win, Qt.Key_Down)
        QTest.keyClick(v.win, Qt.Key_Down)
        self.assertEqual(menu.property("current"), 1)
        QTest.keyClick(v.win, Qt.Key_Return)
        S.pump(150)
        self.assertFalse(menu.property("opened"))
        self.assertEqual(b.cards.get("/w/a/x"), "collapsed")    # "Fold everything inside"
        # near the bottom-right corner the menu flips to stay inside the window
        S.call(v.win, "openFolderMenu", "/w", v.win.contentItem(), 1495, 945)
        S.pump(150)
        self.assertLessEqual(menu.property("x") + menu.property("width"), 1500)
        self.assertLessEqual(menu.property("y") + menu.property("height"), 950)
        QTest.keyClick(v.win, Qt.Key_Escape)
        S.pump(150)
        self.assertFalse(menu.property("opened"))
        v.close()

    def test_wide_menu_row_accepts_click_near_right_edge(self):
        b = S.make_backend()
        b.setFilter("age", 0)
        folder = "/a very long folder name" * 10
        b.apply_scan([S.talk("a", folder, NOW)])
        v = S.View(b)
        self.addCleanup(v.close)
        S.pump(150)
        S.call(v.win, "openFolderMenu", folder, v.win.contentItem(), 100, 100)
        S.pump(150)
        menu = v.find("folderMenu")
        self.assertEqual(menu.property("width"), 480)
        entries = children(menu.property("contentItem"), "menuEntry")
        row = srect(next(entry for entry in entries if entry.property("index") == 1))
        S.click(v.win, row.x() + menu.property("width") - 10, row.center().y())
        S.pump(150)
        self.assertFalse(menu.property("opened"))
        self.assertEqual(b.cards.get(folder), "collapsed")
        for entry in entries:
            self.assertEqual(entry.width(), menu.property("width"))
        self.assertEqual(v.all_warnings(), [])

    def test_g20_shortcuts_work_whatever_has_focus(self):
        b = S.make_backend()
        b.apply_scan([S.talk("a", "/w/a", NOW), S.talk("b", "/w/b", NOW)])
        v = S.View(b)
        S.pump(300)
        v.find("activityPanel").forceActiveFocus()
        self.assertFalse(v.find("treeCanvas").hasActiveFocus())
        b.select("claude:a", False)
        QTest.keyClick(v.win, Qt.Key_Escape)
        S.pump(50)
        self.assertEqual(b.selection, set())
        b.select("claude:a", False)
        v.find("activityPanel").forceActiveFocus()
        QTest.keyClick(v.win, Qt.Key_M)
        S.pump(200)
        dlg = v.find("moveDialog")
        self.assertTrue(dlg.property("opened"))
        # inside a dialog, or while typing, the letters are text
        QTest.keyClick(v.win, Qt.Key_A)
        S.pump(50)
        self.assertTrue(dlg.property("opened"))
        self.assertTrue(v.find("movePath").property("text").endswith("a"))
        S.call(dlg, "close")
        S.pump(200)
        v.close()


if __name__ == "__main__":
    unittest.main()
