"""Panels, keyboard, toasts and the widget input mask (ui.md §8, §11, §14)."""
import unittest
from unittest import mock

import ui_support as S
from ui_support import Qt

from PySide6.QtCore import QRectF
from PySide6.QtGui import QRegion, QWindow
from PySide6.QtTest import QTest

from atree import ops
from atree.qt import widget as W

NOW = S.NOW


def child(item, name):
    return next(it for it in S.walk(item) if it.objectName() == name)


class CalendarFormTest(unittest.TestCase):
    def test_ag20_inputs_clear_only_after_successful_add(self):
        b = S.make_backend()
        agenda = S.FakeAgenda()
        v = S.View(b, agenda=agenda)
        cal = v.find("calendarPanel")
        cal.setProperty("editing", True)
        S.pump(100)
        url, name = child(cal, "calendarUrl"), child(cal, "calendarName")
        S.call(cal, "submit")
        self.assertEqual(agenda.added, [])                   # blank URL
        url.setProperty("text", "https://example.invalid/a.ics")
        name.setProperty("text", "Work")
        S.call(cal, "submit")
        self.assertEqual(agenda.added, [("https://example.invalid/a.ics", "Work")])
        agenda._adding = True
        agenda.changed.emit()
        S.call(cal, "submit")                                 # ignored while adding
        self.assertEqual(len(agenda.added), 1)
        agenda._calendars = [{"id": "x", "name": "Work", "color": "#fff", "failing": False}]
        agenda.changed.emit()
        self.assertEqual(url.property("text"), "https://example.invalid/a.ics")
        agenda._adding, agenda._error = False, "the server answered HTTP 403"
        agenda.changed.emit()
        self.assertEqual(url.property("text"), "https://example.invalid/a.ics")
        agenda._calendars = agenda._calendars + [{"id": "y", "name": "Other", "color": "#fff", "failing": False}]
        agenda.changed.emit()
        self.assertEqual(name.property("text"), "Work")
        agenda._adding, agenda._error = True, ""
        agenda.changed.emit()
        agenda._adding = False
        agenda.changed.emit()
        self.assertEqual((url.property("text"), name.property("text")), ("", ""))
        self.assertEqual(v.all_warnings(), [])
        v.close()


class TimersPanelTest(unittest.TestCase):
    def test_pan9_entry(self):
        b = S.make_backend()
        timers = S.FakeTimers([{"id": "t", "label": "", "when": "done", "alarm": True, "fired": True, "soon": False}])
        v = S.View(b, timers=timers)
        panel = v.find("timersPanel")
        self.assertEqual(panel.property("note"), "done")
        entry = child(panel, "textBoxInput")
        p = S.center(entry)
        S.click(v.win, p.x(), p.y())
        self.assertTrue(entry.hasActiveFocus())
        QTest.keyClick(v.win, Qt.Key_Space)
        QTest.keyClick(v.win, Qt.Key_Return)
        S.pump(50)
        self.assertEqual(panel.property("error"), "try 25m, 1h30, 90s or 14:30")
        S.type_text(v.win, "25m tea")
        self.assertEqual(panel.property("error"), "")
        QTest.keyClick(v.win, Qt.Key_Return)
        S.pump(50)
        self.assertEqual(timers.added[-1], " 25m tea")
        self.assertEqual(entry.property("text"), "")
        S.type_text(v.win, "x")
        QTest.keyClick(v.win, Qt.Key_Escape)
        S.pump(50)
        self.assertEqual(entry.property("text"), "")
        self.assertFalse(entry.hasActiveFocus())
        v.close()


class KeyboardTest(unittest.TestCase):
    def test_key1_letters_do_not_fire_while_typing(self):
        b = S.make_backend()
        talks = [S.talk("a%d" % i, "/w/c%d" % i, NOW - i) for i in range(4)]
        b.apply_scan(talks)
        v = S.View(b)
        S.pump(400)
        card = next(it for it in v.find_all("card") if it.property("path") == "/w/c2")
        r = S.scene_rect(card)
        S.mouse_move(v.win, r.x() + 50, r.y() + 10)
        b.apply_scan(talks + [S.talk("n", "/w/a", NOW, live=True)])
        self.assertTrue(b.layoutPending)
        search = v.find("searchInput")
        search.forceActiveFocus()
        S.type_text(v.win, "f")
        S.pump(50)
        self.assertTrue(b.layoutPending)
        self.assertEqual(search.property("text"), "f")
        S.pump(200)
        self.assertEqual(b.filters["q"], "f")              # filter as you type (150 ms)
        QTest.keyClick(v.win, Qt.Key_Escape)
        S.pump(50)
        self.assertEqual((search.property("text"), b.filters["q"]), ("", ""))
        tree = v.find("treeCanvas")
        self.assertTrue(tree.hasActiveFocus())
        b.apply_scan(talks + [S.talk("n2", "/w/b", NOW, live=True)])
        self.assertTrue(b.layoutPending)
        QTest.keyClick(v.win, Qt.Key_F)
        S.pump(50)
        self.assertFalse(b.layoutPending)
        b.select("claude:a1", False)
        QTest.keyClick(v.win, Qt.Key_Escape)
        S.pump(50)
        self.assertEqual(b.selection, set())
        QTest.keyClick(v.win, Qt.Key_Slash)
        S.pump(50)
        self.assertTrue(search.hasActiveFocus())
        v.close()


class ToastTest(unittest.TestCase):
    def test_toast2_undo_restores(self):
        b = S.make_backend()
        v = S.View(b)
        restored = []
        with mock.patch.object(ops, "restore", side_effect=lambda k: restored.append(k)):
            b.collector = lambda: ([], [])
            b.undoable.emit("archived 1 talk(s)", ["claude:x"], False)
            S.pump(300)
            toast = v.find("toast")
            self.assertTrue(toast.isVisible())
            undo = next(it for it in S.walk(toast) if it.property("text") == "Undo")
            p = S.center(undo)
            S.click(v.win, p.x(), p.y())
            for _ in range(20):
                S.pump(20)
                if restored:
                    break
        self.assertEqual(restored, ["claude:x"])
        self.assertEqual(toast.property("message"), "restored 1 talk(s)")
        self.assertFalse(toast.property("undoKeys").toVariant())
        b.toast.emit("<b>err</b>", True)
        S.pump(50)
        msg = next(it for it in S.walk(toast) if it.property("text") == "<b>err</b>")
        self.assertTrue(S.qml_eval(msg, "textFormat === 0"))
        v.close()


class PlaceholderViewTest(unittest.TestCase):
    def test_draw15_placeholder_row_is_disabled(self):
        b = S.make_backend()
        talks = [S.talk("a", "/w/x", NOW), S.talk("b", "/w/x", NOW - 5)]
        b.apply_scan(talks)
        v = S.View(b)
        b.holdLayout(True)
        b.apply_scan(talks[:1])
        S.pump(100)
        rows = [it for it in v.find_all("talkRow") if it.property("uid") == "claude:b"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertFalse(row.property("enabled"))
        self.assertEqual(row.property("key"), "")
        self.assertEqual(row.property("title"), "No longer in view")
        self.assertEqual(row.property("tip"), "")
        v.close()


class IconHoleTest(unittest.TestCase):
    def test_wid6_7_mask(self):
        win = QWindow()
        win.resize(1000, 800)
        state = {"v": ("top-left", "standard", 3)}

        def poll():
            if state["v"] == "boom":
                raise RuntimeError("gsettings hung")
            return state["v"]

        hole = W.IconHole(win, poll=poll)
        for _ in range(20):
            S.pump(20)
            if hole.state:
                break
        full = QRegion(0, 0, 1000, 800)
        self.assertEqual(win.mask(), full.subtracted(QRegion(0, 0, 136, 366)))
        win.resize(1200, 800)
        S.pump(20)
        self.assertEqual(win.mask(), QRegion(0, 0, 1200, 800).subtracted(QRegion(0, 0, 136, 366)))
        state["v"] = "boom"
        hole.poll()
        S.pump(100)
        self.assertEqual(hole.state, ("top-left", "standard", 3))
        state["v"] = None
        hole.poll()
        for _ in range(20):
            S.pump(20)
            if hole.state is None:
                break
        self.assertTrue(win.mask().isEmpty())
        with mock.patch.object(win, "setMask") as set_mask:
            hole.apply()
            set_mask.assert_not_called()
        del hole
        win.deleteLater()

    def test_nothing_touches_the_window_before_the_first_poll(self):
        import threading
        win = QWindow()
        win.resize(1000, 800)
        win.setProperty("passRect", QRectF(970, 3, 20, 20))
        win.setProperty("desktopPass", True)                  # saved on
        gate = threading.Event()

        def poll():
            gate.wait(5)
            return None                                       # no DING: the state never changes
        with mock.patch.object(win, "setMask") as set_mask:
            hole = W.IconHole(win, poll=poll)
            hole.refresh()                                    # Main.qml's passRect/toggle calls
            win.resize(1100, 800)
            S.pump(50)
            set_mask.assert_not_called()
            self.assertIsNone(win.property("holeTop"))
        gate.set()
        for _ in range(50):
            S.pump(20)
            if hole._ready:
                break
        S.pump(20)
        self.assertEqual(win.mask(), QRegion(970, 3, 20, 20))  # the first poll applies the toggle
        del hole
        win.deleteLater()
        S.pump(20)

    def test_logo_toggle_passes_clicks(self):
        win = QWindow()
        win.resize(1000, 800)
        hole = W.IconHole(win, poll=lambda: ("top-left", "standard", 3))
        for _ in range(20):
            S.pump(20)
            if hole.state:
                break
        self.assertIs(win.property("maskHelper"), hole)
        win.setProperty("passRect", QRectF(970, 3, 20, 20))
        win.setProperty("desktopPass", True)
        hole.refresh()
        self.assertEqual(win.mask(), QRegion(970, 3, 20, 20))
        win.setProperty("desktopPass", False)
        hole.refresh()
        self.assertEqual(win.mask(), QRegion(0, 0, 1000, 800).subtracted(QRegion(0, 0, 136, 366)))
        del hole
        win.deleteLater()
        S.pump(20)


class AgentOrderViewTest(unittest.TestCase):
    """Agent chips and usage blocks: click acts, a sideways drag reorders; Enter asks the leftmost."""

    def drag(self, v, item, dx):
        from PySide6.QtCore import QPoint
        p = S.center(item)
        x, y = int(p.x()), int(p.y())
        QTest.mousePress(v.win, Qt.LeftButton, Qt.NoModifier, QPoint(x, y))
        for i in range(1, 11):
            QTest.mouseMove(v.win, QPoint(x + dx * i // 10, y))
            S.pump(5)
        QTest.mouseRelease(v.win, Qt.LeftButton, Qt.NoModifier, QPoint(x + dx, y))
        S.pump(300)

    def test_click_drag_and_enter(self):
        b = S.make_backend()
        b._askable = {"claude", "codex"}
        b.apply_scan([S.talk("a", "/w/a", NOW), S.talk("c", "/w/c", NOW, tool="codex")])
        b.apply_usage({"claude": {"bars": [{"label": "week", "used": 10}], "observed_at": NOW},
                       "codex": {"bars": [{"label": "week", "used": 20}], "observed_at": NOW}})
        v = S.View(b)
        S.pump(300)

        def chips():
            return sorted(v.find_all("agentChip"), key=lambda c: S.scene_rect(c).x())

        def names():
            return sorted(v.find_all("usageName"), key=lambda n: S.scene_rect(n).x())

        first = chips()[0]
        p = S.center(first)
        S.click(v.win, p.x(), p.y())
        self.assertFalse(b.filters["claude"])                 # a click still toggles
        S.click(v.win, p.x(), p.y())
        self.assertTrue(b.filters["claude"])
        self.drag(v, first, int(S.scene_rect(chips()[1]).right() - p.x()) + 4)
        self.assertEqual([a["name"] for a in b.agents], ["codex", "claude"])
        self.assertEqual([g["key"] for g in b.usage], ["codex", "claude"])
        self.assertTrue(b.filters["claude"])                  # a drag is not a click
        self.assertEqual(b.searchAgent["name"], "codex")
        with mock.patch.object(b, "_spawn") as spawn:
            n = names()
            self.drag(v, n[1], -int(S.scene_rect(n[1]).x() - S.scene_rect(n[0]).x()) - 20)
            self.assertEqual([a["name"] for a in b.agents], ["claude", "codex"])
            spawn.assert_not_called()
            p = S.center(names()[0])
            S.click(v.win, p.x(), p.y())
            self.assertEqual(spawn.call_args[0][2], "claude")  # openAssistant("claude")
            spawn.reset_mock()
            search = v.find("searchInput")
            search.forceActiveFocus()
            S.type_text(v.win, "auth bug")
            QTest.keyClick(v.win, Qt.Key_Return)
            S.pump(50)
            self.assertEqual(spawn.call_args[0][2:4], ("claude", "auth bug"))
            self.assertEqual((b.ai["busy"], b.ai["agent"], b.filters["q"]), (True, "Claude", "auth bug"))
            QTest.keyClick(v.win, Qt.Key_Escape)
            S.pump(50)
            self.assertEqual((b.ai["busy"], b.filters["q"], search.property("text")), (False, "", ""))
        self.assertEqual(v.all_warnings(), [])
        v.close()


if __name__ == "__main__":
    unittest.main()
