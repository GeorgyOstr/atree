"""Stable view, Fit and drag-and-drop in the running view (ui.md §5.6, §6, §8.3)."""
import unittest

import ui_support as S

from PySide6.QtCore import QPointF

NOW = S.NOW


def card(v, path):
    for it in v.find_all("card"):
        if it.property("path") == path:
            return it
    return None


def positions(v):
    return {it.property("path"): (round(S.scene_rect(it).x(), 3), round(S.scene_rect(it).y(), 3))
            for it in v.find_all("card")}


def to_world(v, sx, sy):
    w = v.find("world")
    p = w.mapFromScene(QPointF(sx, sy))
    return p.x(), p.y()


class Base(unittest.TestCase):
    def open(self, talks, size=(1500, 950)):
        self.clock = S.Clock()
        self.b = S.make_backend(self.clock)
        self.talks = talks
        self.b.apply_scan(talks)
        self.v = S.View(self.b, size=size)
        S.mouse_move(self.v.win, 5, 5)
        S.pump(500)
        self.tree = self.v.find("treeCanvas")

    def tearDown(self):
        self.v.close()

    def scan(self, talks, dt=4):
        self.clock.advance(dt)
        self.b.apply_scan([dict(t) for t in talks])
        S.pump(300)

    def rest_on(self, path):
        r = S.scene_rect(card(self.v, path))
        S.mouse_move(self.v.win, r.x() + 60, r.y() + 12)
        S.pump(50)
        self.assertTrue(self.tree.property("layoutHeld"))


def folders(n, per=2, live=()):
    return [S.talk("f%d_%d" % (i, j), "/w/f%02d" % i, NOW - 1000 * i - j, live=(i, j) in live)
            for i in range(n) for j in range(per)]


class HoldViewTest(Base):
    def test_stab9_resting_card_does_not_drift(self):
        self.open(folders(8))
        reading = "/w/f05"
        self.rest_on(reading)
        start = S.scene_rect(card(self.v, reading)).topLeft()
        talks = list(self.talks)
        for i in range(6):
            flip = [dict(t, live=(hash((t["key"], i)) % 3 == 0) and t["cwd"] != reading) for t in talks]
            self.scan(flip)
            now = S.scene_rect(card(self.v, reading)).topLeft()
            self.assertLess(abs(now.x() - start.x()) + abs(now.y() - start.y()), 0.01, "scan %d" % i)

    def test_stab10_new_folder_waits_for_pointer_to_leave(self):
        self.open(folders(6))
        reading = "/w/f03"
        self.rest_on(reading)
        before = S.scene_rect(card(self.v, reading)).topLeft()
        talks = self.talks + [S.talk("new", "/w/aaa", NOW, live=True)]
        self.scan(talks, dt=0)
        self.assertEqual(S.scene_rect(card(self.v, reading)).topLeft(), before)
        self.assertTrue(self.b.layoutPending)
        self.assertTrue(self.v.find("updateTree").isVisible())
        bar = S.scene_rect(self.v.find("sidebar"))
        S.mouse_move(self.v.win, bar.x() + 20, bar.y() + 20)
        S.pump(400)
        self.assertTrue(self.b.held)
        S.pump(700)
        self.assertFalse(self.b.held)
        self.assertFalse(self.b.layoutPending)
        S.pump(300)
        new = S.scene_rect(card(self.v, "/w/aaa"))
        canvas = S.scene_rect(self.tree)
        tree_w = self.tree.property("treeW")
        self.assertGreaterEqual(new.x(), canvas.x())
        self.assertLessEqual(new.right(), canvas.x() + tree_w)
        self.assertGreaterEqual(new.y(), canvas.y())
        self.assertLessEqual(new.bottom(), canvas.bottom())

    def test_stab11_menu_and_drag_hold(self):
        self.open(folders(6))
        S.mouse_move(self.v.win, 5, 5)                # header: pointer outside the tree
        row = next(it for it in self.v.find_all("talkRow") if it.property("key") == "claude:f2_0")
        S.call(self.v.win, "openTalkMenu", "claude:f2_0", row, 0, 0)
        S.pump(200)
        menu = self.v.find("talkMenu")
        self.assertTrue(menu.property("opened"))
        S.pump(900)
        self.assertTrue(self.b.held)
        before = positions(self.v)
        talks = self.talks + [S.talk("new", "/w/aaa", NOW, live=True)]
        self.scan(talks, dt=0)
        self.assertEqual(positions(self.v), before)
        S.call(menu, "close")
        S.pump(200)
        S.call(self.tree, "startDrag", ["claude:f1_0"], "f1_0")
        S.pump(900)
        self.assertTrue(self.b.held)
        self.scan(talks + [S.talk("new2", "/w/aab", NOW, live=True)], dt=0)
        self.assertEqual(positions(self.v), before)
        target = S.scene_rect(card(self.v, "/w/f04"))
        S.call(self.tree, "dragTo", target.x() + 50, target.y() + 40)
        self.assertEqual(self.tree.property("dropPath"), "/w/f04")
        S.call(self.tree, "dragTo", 300, S.scene_rect(self.tree).bottom() - 200)
        self.assertEqual(self.tree.property("dropPath"), "")
        S.call(self.tree, "finishDrag")
        S.pump(1100)
        self.assertFalse(self.b.held)
        self.assertFalse(self.b.layoutPending)


class IdleRefitTest(Base):
    def stale_open(self):
        self.open(folders(6))
        self.tree.setProperty("idleFitAfter", 250)
        self.rest_on("/w/f05")
        talks = [dict(t, updated=NOW + 10) if t["cwd"] == "/w/f05" else t for t in self.talks]
        self.scan(talks)                                   # f05 is now newest, but held in place
        self.assertTrue(self.b.orderIsStale())

    def test_idle_minute_resorts_by_real_recency_and_fits(self):
        self.stale_open()
        S.mouse_move(self.v.win, 5, 5)                     # pointer leaves the tree
        S.pump(1800)
        self.assertFalse(self.b.orderIsStale())
        pos = lambda p: (S.scene_rect(card(self.v, p)).y(), S.scene_rect(card(self.v, p)).x())
        self.assertLess(pos("/w/f05"), pos("/w/f00"))      # first in the grid now
        self.assertFalse(self.tree.property("fitting"))
        S.pump(600)                                        # later idle ticks change nothing
        self.assertFalse(self.tree.property("fitting"))

    def test_no_refit_while_the_pointer_rests_in_the_tree(self):
        self.stale_open()
        y = S.scene_rect(card(self.v, "/w/f05")).y()
        S.pump(1200)
        self.assertTrue(self.b.orderIsStale())
        self.assertEqual(S.scene_rect(card(self.v, "/w/f05")).y(), y)


class FitTest(Base):
    def test_view7_fit_leaves_no_horizontal_range(self):
        self.open(folders(4, per=1))
        cw = self.b.contentWidth
        sidebar_w = self.v.find("sidebar").width()
        tree_w = cw / 1.1
        width = int(tree_w + 12 + sidebar_w + 8)
        self.v.win.resize(max(width, 900), 950)
        S.pump(200)
        tree_w = self.tree.property("treeW")
        self.assertTrue(tree_w < cw <= 1.25 * tree_w, (tree_w, cw))
        S.call(self.tree, "fit", True, None, None)
        S.pump(500)
        self.assertAlmostEqual(self.tree.property("minX"), self.tree.property("maxX"), delta=0.01)
        self.assertFalse(self.v.find("hbar").isVisible())

    def test_double_click_fits_the_width_below_the_readable_floor(self):
        self.open(folders(8, per=1))
        cw = self.b.contentWidth
        sidebar_w = self.v.find("sidebar").width()
        self.v.win.resize(max(int(cw * 0.6 + 80 + 12 + sidebar_w + 8), 900), 950)
        S.pump(200)
        tree_w = self.tree.property("treeW")
        want = (tree_w - 80) / cw
        self.assertLess(want, 0.8, (tree_w, cw))                     # Fit alone would overflow
        canvas = S.scene_rect(self.tree)
        S.call(self.tree, "fit", False, tree_w / 2, canvas.height() / 2)
        S.pump(500)
        world = self.v.find("world")
        self.assertAlmostEqual(world.property("scale"), max(want, 0.3), delta=0.001)
        self.assertAlmostEqual(world.property("x"), 40, delta=0.5)
        self.assertAlmostEqual(self.tree.property("minX"), self.tree.property("maxX"), delta=0.01)
        self.assertFalse(self.v.find("hbar").isVisible())

    def test_view8_double_click_fits_without_reordering(self):
        self.open(folders(12, per=3), size=(1500, 700))
        reading = "/w/f04"
        r = S.scene_rect(card(self.v, reading))
        anchor = to_world(self.v, r.x() + 30, r.y() + 10)
        self.rest_on(reading)
        self.scan(self.talks + [S.talk("new", "/w/aaa", NOW, live=True)], dt=0)
        self.assertTrue(self.b.layoutPending)
        canvas = S.scene_rect(self.tree)
        S.call(self.tree, "fit", False, 300, canvas.height() - 30)
        S.pump(500)
        self.assertTrue(self.b.layoutPending)
        self.assertEqual(self.b.dropAt(*anchor), reading)
        self.b.dropAt(-1e9, -1e9)
        self.assertIsNotNone(card(self.v, reading))

    def test_ctrl0_and_update_tree(self):
        self.open(folders(6))
        self.rest_on("/w/f02")
        self.scan(self.talks + [S.talk("new", "/w/aaa", NOW, live=True)], dt=0)
        btn = self.v.find("updateTree")
        self.assertTrue(btn.isVisible())
        p = S.center(btn)
        S.click(self.v.win, p.x(), p.y())
        S.pump(100)
        self.assertFalse(self.b.layoutPending)


class DragTest(Base):
    def tall(self):
        talks = [S.talk("t%d_%d" % (i, j), "/w/c%02d" % i, NOW - 100 * i - j) for i in range(30) for j in range(5)]
        self.open(talks, size=(1500, 700))

    def test_dnd4_stationary_pointer_retargets(self):
        self.tall()
        c0 = S.scene_rect(card(self.v, "/w/c00"))
        canvas = S.scene_rect(self.tree)
        w = self.v.find("world")
        w.setProperty("y", self.tree.property("minY"))
        S.pump(50)
        S.call(self.tree, "startDrag", ["claude:t0_0"], "t0_0")
        S.call(self.tree, "dragTo", c0.x() + 100, canvas.y() + 15)
        self.assertEqual(self.tree.property("autoDy"), 12)
        seen = [self.tree.property("dropPath")]
        for _ in range(30):
            S.pump(30)
            seen.append(self.tree.property("dropPath"))
        self.assertGreaterEqual(len(set(p for p in seen if p)), 2, seen)
        last = self.tree.property("dropPath")
        lit = {n["path"] for n in self.b._nodes.items() if n["drop"]}
        self.assertEqual(lit, {last} if last else set())
        S.call(self.tree, "dragTo", c0.x() + 100, canvas.y() + 300)
        S.call(self.tree, "finishDrag")

    def test_dnd5_release_retargets_and_opens_dialog(self):
        self.tall()
        S.call(self.tree, "startDrag", ["claude:t0_0", "claude:t1_0"], "two")
        target = S.scene_rect(card(self.v, "/w/c03"))
        S.call(self.tree, "dragTo", target.x() + 80, target.y() + 40)
        self.assertEqual(self.tree.property("dropPath"), "/w/c03")
        # the world moves under a still pointer before release
        w = self.v.find("world")
        w.setProperty("y", w.property("y") - (target.height() + 12))
        under = to_world(self.v, target.x() + 80, target.y() + 40)
        expected = self.b.dropAt(*under)
        self.b.dropAt(-1e9, -1e9)
        S.call(self.tree, "finishDrag")
        S.pump(200)
        self.assertFalse(self.tree.property("dragging"))
        self.assertEqual((self.tree.property("autoDx"), self.tree.property("autoDy")), (0, 0))
        self.assertEqual(self.tree.property("dropPath"), "")
        self.assertIsNone(self.tree.property("dragPoint"))
        self.assertFalse(any(n["drop"] for n in self.b._nodes.items()))
        dlg = self.v.find("moveDialog")
        if expected:
            self.assertTrue(dlg.property("opened"))
            self.assertEqual(list(dlg.property("keys").toVariant()), ["claude:t0_0", "claude:t1_0"])
            box = self.v.find("movePath")
            self.assertEqual(box.property("text"), expected)
            S.call(dlg, "close")
        else:
            self.assertFalse(dlg.property("opened"))
        self.assertNotEqual(expected, "/w/c03")


if __name__ == "__main__":
    unittest.main()
