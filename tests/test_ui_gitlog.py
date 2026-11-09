"""Fixes from the git-log audit (spec/gaps-gitlog.md G-log-2 … G-log-9)."""
import unittest

import ui_support as S

from PySide6.QtGui import QWindow

from atree.qt import widget as W

NOW = S.NOW


def child(item, name):
    return next(it for it in S.walk(item) if it.objectName() == name)


def make(talks=None, **kw):
    b = S.make_backend()
    b.apply_scan(talks or [S.talk("a", "/w/a", NOW, days={})])
    v = S.View(b, **kw)
    S.pump(300)
    return b, v


class IconHoleInsetTest(unittest.TestCase):
    def test_g2_insets_follow_the_hole_corner(self):
        win = QWindow()
        win.resize(1850, 1048)
        hole = W.IconHole(win, poll=lambda: None)
        hole.state = ("bottom-right", "standard", 8)
        left, right, top, _right_top = hole.insets()
        self.assertEqual((left, right), (0, 136))
        self.assertGreater(top, 0)
        hole.state = ("top-left", "standard", 3)
        self.assertEqual(hole.insets()[:3], (136, 0, 0))
        hole.state = None
        self.assertEqual(hole.insets(), (0, 0, 1048, 1048))
        del hole
        win.deleteLater()
        S.pump(20)

    def test_g2_sidebar_details_and_header_stay_out_of_the_hole(self):
        b, v = make(widget=True)
        sidebar = v.find("sidebar")
        x0 = S.scene_rect(sidebar).x()
        v.win.setProperty("holeRight", 136)
        v.win.setProperty("holeTop", 72)
        S.pump(50)
        self.assertAlmostEqual(S.scene_rect(sidebar).right(), v.win.width() - 8 - 136, delta=0.5)
        self.assertAlmostEqual(x0 - S.scene_rect(sidebar).x(), 136, delta=0.5)
        # the hole starts below the header here, so the header keeps its full width
        header = v.find("header")
        self.assertEqual(header.property("rightInset"), 0)
        v.win.setProperty("holeTop", 0)
        S.pump(50)
        self.assertEqual(header.property("rightInset"), 136)
        v.close()


class IconsBelowPanelsTest(unittest.TestCase):
    def test_panels_stay_at_the_edge_and_end_above_low_icons(self):
        b, v = make(widget=True)
        sidebar = v.find("sidebar")
        x0 = S.scene_rect(sidebar).x()
        v.win.setProperty("holeRight", 400)
        v.win.setProperty("holeTop", 692)
        v.win.setProperty("holeRightTop", 692)
        S.pump(50)
        self.assertAlmostEqual(S.scene_rect(sidebar).x(), x0, delta=0.5)
        self.assertLessEqual(S.scene_rect(sidebar).bottom(), 692)
        self.assertEqual(v.find("header").property("rightInset"), 0)
        v.close()

class DialogTest(unittest.TestCase):
    def test_g4_one_talk_list_is_one_row_tall(self):
        b, v = make()
        S.call(v.win, "openMoveDialog", ["claude:a"], S.HOME)
        S.pump(200)
        lst = child(v.find("moveDialog").property("contentItem"), "dialogTalks")
        self.assertEqual(lst.height(), 22)
        v.close()


class TasksDragTest(unittest.TestCase):
    def test_g3_dragging_above_the_first_row_drops_at_index_0(self):
        b, v = make()
        for text in ("one", "two", "three"):
            b.addTask(text)
        S.pump(200)
        panel = v.find("tasksPanel")
        panel.setProperty("dragFrom", 1)
        rows = [it for it in S.walk(panel) if it.objectName() == "taskBox"]
        row_h = rows[0].height() if rows else 26
        # the row's offset is clamped at the top of the list: its midpoint meets the first row's
        top = -S.call(panel, "rowAt", 1).property("y")
        panel.setProperty("dragDy", top)
        self.assertEqual(S.call(panel, "dropIndex"), 0)
        # one row lower it no longer goes first (index 2 = in front of "three": it stays put)
        panel.setProperty("dragDy", top + row_h)
        self.assertNotEqual(S.call(panel, "dropIndex"), 0)
        v.close()


class TimersRowTest(unittest.TestCase):
    def test_g5_label_starts_after_the_icon(self):
        timers = S.FakeTimers([{"id": "t1", "label": "tea", "when": "25m", "alarm": False,
                                "fired": False, "soon": False}])
        b, v = make(timers=timers)
        label = child(v.win.contentItem(), "timerLabel")
        row = label.parentItem()
        self.assertAlmostEqual(label.x(), 28, delta=0.5)
        self.assertLess(label.width(), row.width() - 28)
        v.close()


class BrandTest(unittest.TestCase):
    def test_name_greets_for_two_seconds_then_shows_user_at_host(self):
        import getpass
        import socket
        b, v = make()
        name, who = v.find("brandName"), v.find("brandWho")
        self.assertEqual(name.property("text"), "atree")
        self.assertTrue(name.isVisible())
        self.assertFalse(who.isVisible())
        S.pump(2600)
        self.assertFalse(name.isVisible())
        self.assertTrue(who.isVisible())
        self.assertEqual(who.property("text"),
                         "%s@%s" % (getpass.getuser(), socket.gethostname().split(".")[0]))
        v.close()


class LogoGivesDesktopTest(unittest.TestCase):
    def test_logo_click_fades_atree_and_passes_clicks(self):
        b, v = make(widget=True)
        hole = W.IconHole(v.win, poll=lambda: None)
        S.pump(50)
        logo, header = v.find("logoIcon"), v.find("header")
        self.assertFalse(v.win.property("desktopPass"))
        c = S.center(logo)
        S.click(v.win, c.x(), c.y())
        S.pump(400)
        self.assertTrue(v.win.property("desktopPass"))
        self.assertLess(header.property("fade"), 0.2)
        r = S.scene_rect(logo)
        self.assertTrue(v.win.mask().contains(r.center().toPoint()))
        self.assertFalse(v.win.mask().contains(S.center(v.find("sidebar")).toPoint()))
        c = S.center(logo)
        S.click(v.win, c.x(), c.y())
        S.pump(400)
        self.assertFalse(v.win.property("desktopPass"))
        self.assertEqual(header.property("fade"), 1)
        del hole
        v.close()

    def test_logo_is_inert_in_the_app(self):
        b, v = make()
        c = S.center(v.find("logoIcon"))
        S.click(v.win, c.x(), c.y())
        S.pump(100)
        self.assertFalse(v.win.property("desktopPass"))
        v.close()

    def test_widget_shows_the_logo_alone(self):
        b, v = make(widget=True)
        self.assertFalse(v.find("brand").isVisible())
        S.pump(2600)
        self.assertFalse(v.find("brand").isVisible())
        self.assertTrue(v.find("logoIcon").isVisible())
        v.close()


if __name__ == "__main__":
    unittest.main()
