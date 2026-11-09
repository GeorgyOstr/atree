"""GUI start-up and shutdown contract (ui.md §2, core.md §12.1)."""
import unittest
from unittest import mock

import ui_support as S
from atree.qt import app as A


class Boom(Exception):
    pass


class StartupTest(unittest.TestCase):
    def setUp(self):
        self.log = []
        self.backend = mock.Mock()
        self.backend.shutdown.side_effect = lambda: self.log.append("shutdown")
        self.engine = object()
        self.roots = [object()]
        self.patches = [
            mock.patch.object(A, "request_alpha", side_effect=lambda: self.log.append("alpha")),
            mock.patch.object(A, "make_backend", side_effect=lambda: self.log.append("backend") or self.backend),
            mock.patch.object(A, "make_clock_objects", return_value=(mock.Mock(), mock.Mock())),
            mock.patch.object(A, "make_engine", side_effect=self.make_engine),
            mock.patch.object(A, "load", side_effect=self.load),
            mock.patch.object(A, "destroy_engine", side_effect=lambda e: self.log.append(("destroy", e))),
            mock.patch.object(A, "keep_icons_clickable", side_effect=lambda w: self.log.append(("keep", w)) or "helper"),
            mock.patch.object(A, "run_loop", side_effect=self.run_loop),
        ]
        for p in self.patches:
            p.start()
        self.engine_error = None
        self.load_error = None
        self.loop_error = None
        self.loop_code = 7
        del A._keep[:]

    def tearDown(self):
        for p in self.patches:
            p.stop()
        S.app().setOrganizationName("atree-test")
        del A._keep[:]

    def make_engine(self, backend, widget, timers, agenda):
        self.log.append(("engine", widget))
        if self.engine_error:
            raise self.engine_error
        return self.engine

    def load(self, engine):
        if self.load_error:
            raise self.load_error
        return self.roots

    def run_loop(self, app):
        self.log.append("loop")
        if self.loop_error:
            raise self.loop_error
        return self.loop_code

    def test_app2_normal_exit_returns_loop_code(self):
        self.assertEqual(A.main(), 7)
        self.assertEqual(self.log, ["backend", ("engine", False), "loop", "shutdown", ("destroy", self.engine)])
        self.assertEqual(S.app().organizationName(), "atree")
        self.assertEqual(S.app().applicationName(), "atree")
        self.assertEqual(S.app().desktopFileName(), "atree")

    def test_app6_no_roots_returns_1_without_loop(self):
        self.roots = []
        self.assertEqual(A.main(), 1)
        self.assertNotIn("loop", self.log)
        self.assertEqual(self.log[-2:], ["shutdown", ("destroy", self.engine)])

    def test_app7_cleanup_on_exceptions(self):
        for attr in ("load_error", "loop_error"):
            self.log.clear()
            self.load_error = self.loop_error = None
            setattr(self, attr, Boom(attr))
            with self.assertRaises(Boom):
                A.main()
            self.assertEqual(self.log[-2:], ["shutdown", ("destroy", self.engine)])
            self.assertEqual(self.log.count("shutdown"), 1)

    def test_app7_engine_failure_still_shuts_down(self):
        self.engine_error = Boom("engine")
        with self.assertRaises(Boom):
            A.main()
        self.assertEqual(self.log.count("shutdown"), 1)
        self.assertFalse([e for e in self.log if isinstance(e, tuple) and e[0] == "destroy"])

    def test_app7_shutdown_failure_still_destroys(self):
        def bad():
            self.log.append("shutdown")
            raise Boom("shutdown")
        self.backend.shutdown.side_effect = bad
        with self.assertRaises(Boom):
            A.main()
        self.assertEqual(self.log[-2:], ["shutdown", ("destroy", self.engine)])

    def test_app8_widget_mode(self):
        self.assertEqual(A.main(widget=True), 7)
        self.assertEqual(self.log.count("alpha"), 1)
        self.assertEqual(self.log[0], "alpha")
        self.assertIn(("keep", self.roots[0]), self.log)
        self.assertEqual(A._keep, ["helper"])
        self.assertEqual(S.app().organizationName(), "atree-desktop")
        self.assertIn(("engine", True), self.log)

    def test_app_names_before_application_and_program_name_only(self):
        log = self.log

        class FakeApp:
            def __init__(self, argv):
                log.append(("app", list(argv)))

            @staticmethod
            def instance():
                return None

            def setWindowIcon(self, icon):
                pass

        for name in ("setOrganizationName", "setApplicationName", "setDesktopFileName"):
            setattr(FakeApp, name, staticmethod(lambda v, n=name: log.append((n, v))))
        with mock.patch.object(A, "QGuiApplication", FakeApp), \
                mock.patch.object(A.sys, "argv", ["atree", "-platform", "vnc", "--widget"]):
            A.main()
        names = [e for e in log if isinstance(e, tuple) and e[0].startswith("set")]
        self.assertEqual(names, [("setOrganizationName", "atree"), ("setApplicationName", "atree"),
                                 ("setDesktopFileName", "atree")])
        self.assertLess(log.index(names[-1]), log.index(("app", ["atree"])))

    def test_app_mode_does_not_mask(self):
        A.main(widget=False)
        self.assertNotIn("alpha", self.log)
        self.assertFalse([e for e in self.log if isinstance(e, tuple) and e[0] == "keep"])


if __name__ == "__main__":
    unittest.main()
