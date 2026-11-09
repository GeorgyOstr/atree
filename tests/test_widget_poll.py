"""Dock settings are polled off the GUI thread, even for manually placed icons."""
import threading
import unittest
from unittest import mock

import ui_support as S
from PySide6.QtCore import QRect
from PySide6.QtGui import QRegion, QWindow

from atree.qt import widget as W


class WidgetPollTest(unittest.TestCase):
    def test_apply_and_resize_use_cached_dock_settings(self):
        win = QWindow()
        win.resize(1000, 800)
        self.addCleanup(win.deleteLater)
        state = ("top-left", "standard", 1, ((500, 450),))
        dock = {"value": "LEFT"}
        threads = []

        def run(*args, **kwargs):
            threads.append(threading.get_ident())
            return mock.Mock(returncode=0, stdout="'%s'\n" % dock["value"])

        def settle(hole):
            for _ in range(100):
                S.pump(10)
                if not hole._busy:
                    return
            self.fail("settings poll did not finish")

        screen = mock.Mock()
        screen.geometry.return_value = QRect(100, 200, 1200, 900)
        with mock.patch.object(win, "screen", return_value=screen), \
                mock.patch.object(W.subprocess, "run", side_effect=run) as process:
            hole = W.IconHole(win, poll=lambda: state)
            self.addCleanup(hole._timer.stop)
            settle(hole)
            self.assertTrue(threads)
            self.assertNotIn(threading.get_ident(), threads)
            self.assertEqual(hole.rects(), [(192, 142, 136, 122)])
            process.reset_mock()
            hole.apply()
            win.resize(900, 700)
            S.pump(20)
            self.assertEqual(hole.rects(), [(92, 42, 136, 122)])
            self.assertEqual(win.mask(), QRegion(0, 0, 900, 700).subtracted(QRegion(92, 42, 136, 122)))
            process.assert_not_called()
            # a dock change alone must update the mask, even when the icons did not move
            dock["value"] = "BOTTOM"
            hole.poll()
            settle(hole)
            self.assertEqual(hole.rects(), [(392, 210, 136, 122)])
            self.assertEqual(win.mask(), QRegion(0, 0, 900, 700).subtracted(QRegion(392, 210, 136, 122)))
            self.assertNotIn(threading.get_ident(), threads)


if __name__ == "__main__":
    unittest.main()
