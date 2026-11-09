"""Claude Code status-line hook, installer and atomic private writes (spec clock.md §1, §11)."""
import contextlib
import io
import json
import os
import shlex
import stat
import sys
import threading
import unittest
from unittest import mock

from clocksupport import ROOT, HomeCase, read, write

from atree import fsutil, statusline

OURS = "/usr/bin/python3.14t /opt/atree.py --statusline"
OTHER_OURS = "'/new tree/.venv/bin/python3' '/new tree/atree.py' --statusline"


class Stdin:
    def __init__(self, data):
        self.buffer = io.BytesIO(data)


class Untouchable:
    @property
    def buffer(self):
        raise AssertionError("stdin was read")


def tmp_name(path):
    return os.path.join(os.path.dirname(path), ".%s.%d.%d.tmp" % (os.path.basename(path), os.getpid(),
                                                                  threading.get_ident()))


class AtomicWrites(HomeCase):
    def test_R_io_01_R_io_02_atomic_and_parent_created(self):
        target = self.path("deep", "dir", "f.json")
        names = []
        real = os.replace
        with mock.patch.object(fsutil.os, "replace", lambda a, b: names.append(a) or real(a, b)):
            fsutil.atomic_write(target, "one")
        self.assertEqual(read(target), b"one")
        self.assertEqual(names, [tmp_name(target)])

    def test_R_io_03_stale_temporary(self):
        target = write(self.path("f"), "old")
        write(tmp_name(target), "stale")
        with self.assertRaises(FileExistsError):
            fsutil.atomic_write(target, "new")
        self.assertEqual(read(target), b"old")
        self.assertFalse(os.path.exists(tmp_name(target)))
        fsutil.atomic_write(target, "new")
        self.assertEqual(read(target), b"new")

    def test_R_io_04_R_io_06_private_mode_from_creation(self):
        target = write(self.path("f"), "old")
        os.chmod(target, 0o644)
        for umask in (0, 0o22):
            modes = []
            real = os.fdopen

            def spy(fd, *a, **k):
                modes.append(stat.S_IMODE(os.fstat(fd).st_mode))
                return real(fd, *a, **k)
            old = os.umask(umask)
            try:
                with mock.patch.object(fsutil.os, "fdopen", spy):
                    fsutil.atomic_write(target, "secret", 0o600)
            finally:
                os.umask(old)
            self.assertEqual(modes, [0o600])
            self.assertEqual(stat.S_IMODE(os.stat(target).st_mode), 0o600)
        old = os.umask(0o22)
        try:
            fsutil.atomic_write(self.path("plain"), "x")
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(self.path("plain")).st_mode), 0o644)

    def test_R_io_05_failure_leaves_target(self):
        target = write(self.path("f"), "old")
        with mock.patch.object(fsutil.os, "fdopen", side_effect=OSError("cannot open")):
            with self.assertRaises(OSError):
                fsutil.atomic_write(target, "new")
        self.assertEqual(read(target), b"old")
        self.assertEqual([n for n in os.listdir(self.home) if n.endswith(".tmp")], [])


class Hook(HomeCase):
    def run_hook(self, payload, stdin_cls=Stdin):
        with mock.patch.object(sys, "stdin", stdin_cls(payload) if stdin_cls is Stdin else stdin_cls()):
            return statusline.run()

    def test_R_sl_01_recursion_guard(self):
        with mock.patch.dict(os.environ, {"ATREE_STATUSLINE": "1"}):
            self.assertEqual(self.run_hook(None, Untouchable), 0)

    def test_R_sl_02_saves_limits_privately(self):
        self.assertEqual(self.run_hook(b'{"rate_limits": {"five_hour": {"used_percentage": 3}}, "model": "x"}'), 0)
        saved = json.loads(read(statusline.LIMITS))
        self.assertEqual(saved["rate_limits"], {"five_hour": {"used_percentage": 3}})
        self.assertIsInstance(saved["at"], float)
        self.assertEqual(stat.S_IMODE(os.stat(statusline.LIMITS).st_mode), 0o600)
        self.assertTrue(read(statusline.LIMITS).startswith(b'{\n  "at"'))

    def test_R_sl_02_ignores_other_payloads(self):
        for payload in (b"", b"{", b"[]", b"{}", b'{"rate_limits": {}}', b'{"rate_limits": [1]}', b"\xff"):
            self.assertEqual(self.run_hook(payload), 0)
            self.assertFalse(os.path.exists(statusline.LIMITS), payload)

    def test_R_sl_02_write_failure_ignored(self):
        with mock.patch.object(statusline, "atomic_write", side_effect=OSError("disk full")):
            self.assertEqual(self.run_hook(b'{"rate_limits": {"a": {}}}'), 0)

    def test_R_sl_03_R_sl_04_runs_previous_command(self):
        log = self.path("child")
        command = "cat > %s.in; echo \"$ATREE_STATUSLINE\" >> %s.env; exit 7" % (log, log)
        write(statusline.NEXT, json.dumps({"command": command}))
        payload = b'{"rate_limits": {"five_hour": {"used_percentage": 12}}}'
        self.assertEqual(self.run_hook(payload), 7)
        self.assertEqual(read(log + ".in"), payload)
        self.assertEqual(read(log + ".env"), b"1\n")
        self.assertNotIn("ATREE_STATUSLINE", os.environ)
        self.assertEqual(statusline.limits()["rate_limits"], {"five_hour": {"used_percentage": 12}})

    def test_R_sl_03_unusable_next(self):
        for content in ("{", "[]", "null", '{"command": ["bad"]}', '{"command": ""}', ""):
            write(statusline.NEXT, content)
            with mock.patch.object(statusline.subprocess, "run", side_effect=AssertionError("ran")):
                self.assertEqual(self.run_hook(b"{}"), 0, content)

    def test_R_sl_05_own_hook_as_next(self):
        inner = "%s -c %s" % (shlex.quote(sys.executable), shlex.quote(
            "import sys; sys.path.insert(0, %r); from atree import statusline; sys.exit(statusline.run())" % ROOT))
        write(statusline.NEXT, json.dumps({"command": inner}))
        self.assertEqual(self.run_hook(b"{}"), 0)
        self.assertFalse(os.path.exists(statusline.LIMITS))

    def test_R_sl_06_limits(self):
        self.assertIsNone(statusline.limits())
        for content in ("[]", '{"rate_limits": []}', "{"):
            write(statusline.LIMITS, content)
            self.assertIsNone(statusline.limits())
        write(statusline.LIMITS, '{"at": 5, "rate_limits": {}}')
        self.assertEqual(statusline.limits(), {"at": 5, "rate_limits": {}})


class Ownership(unittest.TestCase):
    def test_R_sl_07_is_ours(self):
        for cmd in (OTHER_OURS, OURS, "/opt/atree.py --statusline"):
            self.assertTrue(statusline.is_ours(cmd), cmd)
        for cmd in ("other --statusline", "python other.py --statusline", "python atree.py --statusline extra",
                    "'broken --statusline", None, "--statusline"):
            self.assertFalse(statusline.is_ours(cmd), cmd)


class Install(HomeCase):
    def settings(self):
        return json.loads(read(statusline.SETTINGS))

    def put_settings(self, obj):
        write(statusline.SETTINGS, json.dumps(obj) if not isinstance(obj, (str, bytes)) else obj)

    def call(self, fn, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = fn(*args)
        return code, out.getvalue() + err.getvalue()

    def test_R_sl_08_invalid_settings(self):
        for content in ("[]", "false", "null", '"text"', "{"):
            self.put_settings(content)
            code, msg = self.call(statusline.install, OURS)
            self.assertNotEqual(code, 0)
            self.assertIn("%s isn't a valid JSON object: fix it and run this again" % statusline.SETTINGS, msg)
            self.assertEqual(read(statusline.SETTINGS), content.encode())
            self.assertFalse(os.path.exists(statusline.NEXT))

    def test_R_sl_08_missing_settings(self):
        code, msg = self.call(statusline.install, OURS)
        self.assertEqual(code, 0)
        self.assertEqual(self.settings(), {"statusLine": {"type": "command", "command": OURS}})
        self.assertEqual(json.loads(read(statusline.NEXT)), {"command": None})
        self.assertFalse(os.path.exists(statusline.BACKUP))

    def test_R_sl_09_R_sl_10_R_sl_11_round_trip(self):
        for prev in ("my-status --statusline", "printf hello", "'/other tool/status' --statusline"):
            line = {"type": "command", "command": prev, "padding": 1}
            original = {"other": True, "statusLine": line}
            self.put_settings(original)
            code, msg = self.call(statusline.install, OURS)
            self.assertEqual(code, 0)
            self.assertEqual(msg, "Claude Code's status line now feeds atree's usage bars, then runs: %s\n"
                                  "they fill in once a Claude session refreshes its status line\n" % prev)
            self.assertEqual(json.loads(read(statusline.NEXT)), {"command": prev, "statusLine": line})
            self.assertEqual(self.settings(), {"other": True, "statusLine": dict(line, command=OURS)})
            code, msg = self.call(statusline.uninstall)
            self.assertEqual((code, msg), (0, "restored Claude Code's status line: %s\n" % prev))
            self.assertEqual(self.settings(), original)
            self.assertFalse(os.path.exists(statusline.NEXT))

    def test_R_sl_12_other_kinds(self):
        for original in ({"type": "static", "text": "hi", "padding": 2}, {"command": "untyped"}, "plain string", None):
            self.put_settings({"other": True, "statusLine": original})
            self.call(statusline.install, OURS)
            self.assertEqual(self.settings()["statusLine"], {"type": "command", "command": OURS})
            self.call(statusline.uninstall)
            self.assertEqual(self.settings(), {"other": True, "statusLine": original})

    def test_R_sl_13_relocated_reinstall(self):
        original = {"statusLine": {"type": "command", "command": "original-status"}}
        self.put_settings(original)
        self.call(statusline.install, OURS)
        self.call(statusline.install, OTHER_OURS)
        code, msg = self.call(statusline.install, OTHER_OURS)
        self.assertNotIn("then runs", msg)
        self.call(statusline.uninstall)
        self.assertEqual(self.settings(), original)

    def test_R_sl_14_privacy(self):
        raw = (b'{\r\n  "env": {"TOKEN": "s3cret"},\r\n  "statusLine": {"type": "command", '
               b'"command": "status --token abc"}\r\n}\r\n')
        self.put_settings(raw)
        os.chmod(statusline.SETTINGS, 0o644)
        writes = []
        real = statusline.atomic_write

        def spy(path, data, mode=None):
            writes.append((path, mode))
            return real(path, data, mode)
        old = os.umask(0o22)
        try:
            with mock.patch.object(statusline, "atomic_write", spy):
                self.call(statusline.install, OURS)
                for p in (statusline.SETTINGS, statusline.BACKUP, statusline.NEXT):
                    self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600, p)
                self.assertEqual(read(statusline.BACKUP), raw)
                self.assertEqual(json.loads(read(statusline.NEXT)),
                                 {"command": "status --token abc", "statusLine": json.loads(raw)["statusLine"]})
                self.call(statusline.uninstall)
        finally:
            os.umask(old)
        self.assertEqual(writes, [(statusline.NEXT, 0o600), (statusline.BACKUP, 0o600),
                                  (statusline.SETTINGS, 0o600), (statusline.SETTINGS, 0o600)])
        self.assertEqual(self.settings(), json.loads(raw))
        self.assertEqual(stat.S_IMODE(os.stat(statusline.SETTINGS).st_mode), 0o600)
        self.assertEqual(read(statusline.BACKUP), raw)
        self.assertFalse(os.path.exists(statusline.NEXT))

    def test_R_sl_15_not_installed(self):
        for settings in ({}, {"statusLine": "x"}, {"statusLine": {"type": "command", "command": "other"}}):
            self.put_settings(settings)
            self.assertEqual(self.call(statusline.uninstall), (0, "atree isn't in Claude Code's status line\n"))
            self.assertEqual(self.settings(), settings)
        self.put_settings("[]")
        self.assertNotEqual(self.call(statusline.uninstall)[0], 0)

    def test_R_sl_16_invalid_next(self):
        settings = {"statusLine": {"type": "command", "command": OURS}}
        for content, msg in (("[]", "isn't a valid JSON object"), ("null", "isn't a valid JSON object"),
                             ("{", "isn't a valid JSON object"), ('{"command": ["bad"]}', "has an invalid command")):
            self.put_settings(settings)
            write(statusline.NEXT, content)
            code, out = self.call(statusline.uninstall)
            self.assertNotEqual(code, 0)
            self.assertIn("%s %s: fix it and run this again" % (statusline.NEXT, msg), out)
            self.assertEqual(read(statusline.NEXT), content.encode())
            self.assertEqual(self.settings(), settings)

    def test_R_sl_17_restore_rules(self):
        ours = {"type": "command", "command": OURS, "padding": 1}
        self.put_settings({"statusLine": ours})
        write(statusline.NEXT, '{"command": "older-status"}')
        self.assertEqual(self.call(statusline.uninstall), (0, "restored Claude Code's status line: older-status\n"))
        self.assertEqual(self.settings(), {"statusLine": dict(ours, command="older-status")})
        # rule 3: no next file, the backup has a statusLine
        self.put_settings({"statusLine": ours, "added": 1})
        write(statusline.BACKUP, '{"statusLine": "from backup", "old": 2}')
        code, msg = self.call(statusline.uninstall)
        self.assertEqual(msg, "restored Claude Code's status line from %s\n" % statusline.BACKUP)
        self.assertEqual(self.settings(), {"statusLine": "from backup", "added": 1})
        # rule 1 without a previous command
        self.put_settings({"statusLine": ours})
        write(statusline.NEXT, '{"command": null, "statusLine": null}')
        self.assertEqual(self.call(statusline.uninstall), (0, "restored Claude Code's status line\n"))
        self.assertEqual(self.settings(), {"statusLine": None})

    def test_R_sl_18_next_beats_old_backup(self):
        self.put_settings({"statusLine": {"type": "command", "command": "original-status"}})
        self.call(statusline.install, OURS)
        self.call(statusline.uninstall)
        os.unlink(statusline.SETTINGS)
        self.call(statusline.install, OURS)
        code, msg = self.call(statusline.uninstall)
        self.assertEqual(msg, "removed atree from Claude Code's status line\n")
        self.assertEqual(self.settings(), {})

    def test_R_sl_19_missing_next_and_backup(self):
        self.put_settings({"other": True, "statusLine": {"type": "command", "command": OURS}})
        code, msg = self.call(statusline.uninstall)
        self.assertEqual(self.settings(), {"other": True})
        self.assertIn("is missing", msg)

    def test_R_sl_20_stale_temporaries(self):
        original = {"statusLine": {"type": "command", "command": "original-status"}}
        self.put_settings(original)
        before = read(statusline.SETTINGS)
        write(tmp_name(statusline.NEXT), "stale")
        with self.assertRaises(FileExistsError):
            self.call(statusline.install, OURS)
        self.assertEqual(read(statusline.SETTINGS), before)
        self.assertFalse(os.path.exists(tmp_name(statusline.NEXT)))
        self.assertEqual(self.call(statusline.install, OURS)[0], 0)
        self.assertEqual(stat.S_IMODE(os.stat(statusline.NEXT).st_mode), 0o600)
        installed = read(statusline.SETTINGS)
        write(tmp_name(statusline.SETTINGS), "stale")
        with self.assertRaises(FileExistsError):
            self.call(statusline.uninstall)
        self.assertEqual(read(statusline.SETTINGS), installed)
        self.assertFalse(os.path.exists(tmp_name(statusline.SETTINGS)))
        self.assertEqual(self.call(statusline.uninstall)[0], 0)
        self.assertEqual(self.settings(), original)


if __name__ == "__main__":
    unittest.main()
