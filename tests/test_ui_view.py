"""The Qt Quick view loaded with the real backend (ui.md §3, §5.6, §6, §8, §11, §13)."""
import datetime
import json
import os
import shutil
import unittest

import ui_support as S
from ui_support import Qt

from PySide6.QtCore import QSettings
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtQml import QQmlNetworkAccessManagerFactory

NOW = S.NOW
MARKUP = "<img src=\"https://example.invalid/x.png\"> <b>&amp;</b>"


def world_state(v):
    w = v.find("world")
    return (w.property("x"), w.property("y"), w.property("scale"))


def node_item(v, path, kind="card"):
    name = "card" if kind == "card" else "folderPill"
    for it in v.find_all(name):
        if it.property("path") == path:
            return it
    raise AssertionError("no %s item for %s" % (kind, path))


def wide_talks(n_folders=9, per=1, base="/w"):
    return [S.talk("t%d_%d" % (i, j), "%s/c%02d" % (base, i), NOW - i * 100 - j)
            for i in range(n_folders) for j in range(per)]


class LoadTest(unittest.TestCase):
    def test_idle3_loads_without_warnings_app_and_widget(self):
        for widget in (False, True):
            b = S.make_backend()
            b.apply_scan(wide_talks(3, 2) + [S.talk("arch", "/w/c00", NOW, archived=True)])
            v = S.View(b, widget=widget)
            S.pump(300)
            self.assertEqual(v.all_warnings(), [], "widget=%s" % widget)
            self.assertEqual(v.win.title(), "atree Desktop" if widget else "atree")
            v.close()

    def test_real_backend_under_fixture_home(self):
        from atree.qt import app as A
        from atree.qt.backend import Backend
        proj = os.path.join(S.HOME, "project")
        os.makedirs(proj, exist_ok=True)
        folder = os.path.join(os.environ["CLAUDE_CONFIG_DIR"], "projects", "project")
        os.makedirs(folder, exist_ok=True)
        with open(os.path.join(folder, "claude-fixture.jsonl"), "w") as f:
            f.write(json.dumps({"type": "user", "cwd": proj, "sessionId": "claude-fixture",
                                "timestamp": "2026-01-01T12:00:00Z",
                                "message": {"role": "user", "content": "hello " + MARKUP}},
                               separators=(",", ":")) + "\n")
        b = Backend()
        timers, agenda = A.make_clock_objects()
        v = S.View(b, timers=timers, agenda=agenda)
        for _ in range(100):
            S.pump(50)
            if b.talks:
                break
        S.pump(300)
        self.assertTrue(any(t["tool"] == "claude" for t in b.talks), b.talks)
        self.assertEqual(v.all_warnings(), [])
        v.close()
        for obj in (timers, agenda):
            obj.shutdown()
        self.assertFalse([c for c in S.calls() if not c.startswith("gsettings")], S.calls())
        shutil.rmtree(folder)


class Recorder(QNetworkAccessManager):
    requests = []

    def createRequest(self, op, request, data=None):
        Recorder.requests.append(request.url().toString())
        return super().createRequest(op, request, data)


class Factory(QQmlNetworkAccessManagerFactory):
    def create(self, parent):
        return Recorder(parent)


class PlainTextTest(unittest.TestCase):
    def test_text1_2_3_external_text_is_plain(self):
        b = S.make_backend()
        talks = [S.talk("a", "/w/" + MARKUP, NOW - 5, title=MARKUP, first_msg="line1\n" + MARKUP,
                        branch=MARKUP, live=True, live_status=MARKUP),
                 S.talk("b", "/w/x", NOW - 10, title="plain")]
        b.apply_scan(talks)
        b.addTask(MARKUP)
        b.apply_usage({"claude": {"error": MARKUP, "bars": [{"label": MARKUP, "used": 5}]}, "at": NOW})
        b._ai = {"busy": False, "agent": "Claude", "query": MARKUP, "results": [
            {"key": "claude:a", "why": MARKUP, "title": MARKUP, "folder": MARKUP, "tool": "claude"}], "error": ""}
        b.aiChanged.emit()
        timers = S.FakeTimers([{"id": "1", "label": MARKUP, "when": "4m", "alarm": False, "fired": False, "soon": False}])
        agenda = S.FakeAgenda(events=[{"head": True, "title": "Today"},
                                      {"head": False, "title": MARKUP, "color": "#ff0000", "now": True,
                                       "time": "10:00", "until": MARKUP}],
                              calendars=[{"id": "c", "name": MARKUP, "color": "#00ff00", "failing": True}])
        Recorder.requests = []
        from atree.qt import app as A
        orig = A.make_engine

        def make(*a):
            e = orig(*a)
            self.factory = Factory()
            e.setNetworkAccessManagerFactory(self.factory)
            return e

        A.make_engine = make
        try:
            v = S.View(b, timers=timers, agenda=agenda)
        finally:
            A.make_engine = orig
        b.select("claude:a", False)
        b.toast.emit(MARKUP, True)
        S.pump(300)
        cal = v.find("calendarPanel")
        cal.setProperty("editing", True)
        S.pump(100)
        cal.setProperty("editing", False)
        tip = v.find("sharedTip")
        row = next(it for it in v.find_all("talkRow") if it.property("key") == "claude:a")
        S.call(tip, "ask", row, "prompt\n" + MARKUP, 1)
        S.pump(100)
        tip_text = v.find("sharedTipText")
        self.assertEqual(tip_text.property("text"), "prompt\n" + MARKUP)
        self.assertTrue(S.qml_eval(tip_text, "textFormat === 0"))
        seen = 0
        for it in S.walk(v.win.contentItem()):
            text = it.property("text")
            if isinstance(text, str) and "<img" in text and it.metaObject().indexOfProperty("textFormat") >= 0:
                seen += 1
                self.assertTrue(S.qml_eval(it, "textFormat === 0"), "%s: %r" % (it.metaObject().className(), text))
        self.assertGreaterEqual(seen, 8)
        self.assertEqual(Recorder.requests, [])
        self.assertEqual(v.all_warnings(), [])
        v.close()


class IdleTest(unittest.TestCase):
    def test_idle1_unchanged_scans_draw_nothing(self):
        b = S.make_backend()
        talks = wide_talks(4, 3) + [S.talk("live", "/w/c00", NOW - 1, live=True)]
        b.apply_scan(talks)
        v = S.View(b)
        S.mouse_move(v.win, 5, 5)          # header: the tree is not held
        S.pump(2600)                       # past the start-up greeting's fade
        frames = []
        v.win.frameSwapped.connect(lambda: frames.append(1))
        changed = []
        b.changed.connect(lambda: changed.append(1))
        for _ in range(11):
            b.apply_scan([dict(t) for t in talks])
            S.pump(100)
        self.assertEqual((frames, changed), ([], []))
        b.apply_scan(talks + [S.talk("new", "/w/zz", NOW, live=True)])
        S.pump(400)
        self.assertTrue(frames)
        v.close()


class SidebarTest(unittest.TestCase):
    def make(self, height=950, tasks=0, events=0):
        b = S.make_backend()
        b.apply_scan(wide_talks(15, 4))
        for i in range(tasks):
            b.addTask("task %d" % i)
        ev = []
        for i in range(events):
            ev.append({"head": False, "title": "event %d" % i, "color": "#ff0000", "now": False,
                       "time": "%02d:00" % (i % 24), "until": ""})
        agenda = S.FakeAgenda(events=ev, calendars=[{"id": "c", "name": "cal", "color": "#fff", "failing": False}])
        v = S.View(b, agenda=agenda, size=(1500, height))
        S.pump(500)
        return b, v

    def tearDown(self):
        for t in list(S.app().allWindows()):
            pass

    def test_win10_wheel_over_sidebar_never_moves_tree(self):
        b, v = self.make(height=600)
        bar = v.find("sidebar")
        act = v.find("activityPanel")
        p = S.center(act)
        before = world_state(v)
        for mods in (Qt.NoModifier, Qt.ControlModifier, Qt.ShiftModifier):
            for dy in (-120, 120, -1200, 1200):
                S.wheel(v.win, p.x(), p.y(), dy=dy, modifiers=mods)
        S.wheel(v.win, p.x(), p.y(), pixel=-40)
        S.pump(300)
        self.assertEqual(world_state(v), before)
        self.assertGreaterEqual(bar.property("offset"), 0)
        # below the sidebar the wheel pans the tree as usual
        r = S.scene_rect(bar)
        S.wheel(v.win, p.x(), r.bottom() + 20, pixel=-60)
        S.pump(50)
        self.assertNotEqual(world_state(v), before)
        v.close()

    def test_pan16_calendar_list_then_sidebar(self):
        b, v = self.make(height=600, events=40)
        bar = v.find("sidebar")
        flick = v.find("calendarList")
        self.assertGreater(flick.property("contentHeight"), flick.height())
        p = S.center(flick)
        S.wheel(v.win, p.x(), p.y(), dy=-120)
        self.assertGreater(flick.property("contentY"), 0)
        self.assertEqual(bar.property("offset"), 0)
        before = world_state(v)
        for _ in range(60):
            p = S.center(flick)
            S.wheel(v.win, p.x(), p.y(), dy=-120)
        self.assertGreater(bar.property("offset"), 0)
        self.assertAlmostEqual(flick.property("contentY"), flick.property("contentHeight") - flick.height(), delta=1)
        self.assertEqual(world_state(v), before)
        # touchpad over the calendar title scrolls the sidebar
        off = bar.property("offset")
        cal = v.find("calendarPanel")
        top = S.scene_rect(cal)
        S.wheel(v.win, top.x() + 40, max(top.y() + 10, S.scene_rect(bar).y() + 5), pixel=60)
        self.assertLess(bar.property("offset"), off)
        v.close()

    def test_task5_task_list_then_sidebar(self):
        b, v = self.make(height=600, tasks=40)
        bar = v.find("sidebar")
        self.assertIsNotNone(v.find("tasksPanel"))
        bar.setProperty("offset", bar.property("maxOffset"))
        S.pump(50)
        lst = v.find("taskList")
        self.assertGreater(lst.property("contentHeight"), lst.height())
        before = world_state(v)
        p = S.center(lst)
        S.wheel(v.win, p.x(), p.y(), dy=-120)
        self.assertGreater(lst.property("contentY"), 0)
        for _ in range(60):
            S.wheel(v.win, p.x(), p.y(), dy=120)
        self.assertEqual(lst.property("contentY"), 0)
        self.assertLess(bar.property("offset"), bar.property("maxOffset"))
        self.assertEqual(world_state(v), before)
        v.close()

    def test_foc3_click_focuses_task_entry(self):
        b, v = self.make()
        entry = v.find("taskEntry")
        inp = next(it for it in S.walk(entry) if it.objectName() == "textBoxInput")
        p = S.center(entry)
        S.click(v.win, p.x(), p.y())
        self.assertTrue(inp.hasActiveFocus())
        texts = [it.property("text") for it in S.walk(entry) if isinstance(it.property("text"), str)]
        self.assertIn("Add a task…", texts)
        # clicking the tree hands focus back to the canvas
        S.click(v.win, 300, 900)
        self.assertFalse(inp.hasActiveFocus())
        self.assertTrue(v.prop("tree").hasActiveFocus())
        v.close()

    def test_panels_fold_persist_and_keep_selection(self):
        b, v = self.make(height=600, tasks=30)
        b.select("claude:t1_0", False)
        S.pump(50)
        bar = v.find("sidebar")
        bar.setProperty("offset", 150)
        S.pump(50)
        settings = v.prop("viewSettings")
        names = ["activityPanel", "timersPanel", "calendarPanel", "tasksPanel"]
        keys = ["activityFolded", "timersFolded", "calendarFolded", "tasksFolded"]
        # the details panel covers the sidebar while a talk is shown; clear its way
        for name, key in zip(names, keys):
            panel = v.find(name)
            title = next(it for it in S.walk(panel) if it.objectName() == "panelTitle")
            if S.scene_rect(title).y() < S.scene_rect(bar).y():
                bar.setProperty("offset", 0)
                S.pump(50)
            v.find("detailsPanel").setVisible(False)
            p = S.center(title)
            S.click(v.win, p.x() - 60, p.y())
            S.pump(300)
            self.assertTrue(settings.property(key), key)
            self.assertTrue(panel.property("folded"), key)
        self.assertEqual(b.selection, {"claude:t1_0"})
        S.pump(300)
        self.assertEqual(bar.property("offset"), 0)
        v.close()
        s = QSettings("atree-test", "atree")
        for key in keys:
            self.assertIn(str(s.value("view/" + key)).lower(), ("true", "1"))
            s.setValue("view/" + key, False)
        s.sync()

    def test_pan2_6_activity_width(self):
        b = S.make_backend()
        b.apply_scan([S.talk("a", "/w/a", NOW)])
        v = S.View(b)
        act = v.find("activityPanel")
        self.assertEqual(act.width(), 320)
        v.close()
        b = S.make_backend()
        b.apply_scan([S.talk("a", "/w/a", NOW), S.talk("b", "/w/b", NOW, tool="codex"),
                      S.talk("c", "/w/c", NOW, tool="gemini")])
        v = S.View(b)
        act = v.find("activityPanel")
        content = v.find("activityContent")
        self.assertGreater(act.width(), 320)
        self.assertGreaterEqual(act.width() - (content.x() + content.width()), 12)
        self.assertGreaterEqual(v.find("sidebar").width(), act.width())
        v.close()

    def test_pan13_calendar_boxes(self):
        b, v = self.make()
        cal = v.find("calendarPanel")
        cal.setProperty("editing", True)
        S.pump(400)
        panel = S.scene_rect(cal)
        for name in ("calendarUrl", "calendarName"):
            box = next(it for it in S.walk(cal) if it.objectName() == name)
            r = S.scene_rect(box)
            self.assertGreaterEqual(r.width(), 240, name)
            self.assertTrue(panel.contains(r), name)
        v.close()

    def test_ui01_activity_cells(self):
        for today in (datetime.date(2026, 7, 10), datetime.date(2026, 10, 10), datetime.date(2026, 4, 10)):
            first_last = (today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
            outside = first_last.weekday()
            self.assertIn(outside, (0, 1, 6))
            clock = S.Clock(now=datetime.datetime.combine(today, datetime.time(12)).timestamp())
            b = S.make_backend(clock)
            b.apply_scan([S.talk("a", "/w/a", clock.now)])
            v = S.View(b)
            weeks = [w for w in v.find_all("activityWeek") if w.x() == 0]
            self.assertTrue(weeks)
            cells = sorted((c for c in weeks[0].childItems() if c.objectName() == "activityCell"), key=lambda c: c.y())
            self.assertEqual([c.y() for c in cells], [15 * d for d in range(7)])
            for d, c in enumerate(cells):
                self.assertEqual((c.width(), c.height()), (13, 13))
                area, = [a for a in c.childItems() if a.metaObject().className().startswith("QQuickMouseArea")]
                if d < outside:
                    self.assertEqual(c.opacity(), 0)
                    self.assertFalse(area.property("enabled"))
                else:
                    self.assertEqual(c.opacity(), 1)
                    self.assertTrue(area.property("enabled"))
            self.assertEqual(v.all_warnings(), [])
            v.close()


if __name__ == "__main__":
    unittest.main()
