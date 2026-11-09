"""Claude moves keep sessions together, and literal config paths retain liveness."""
import errno
import json
import os
from unittest import mock

from support import HomeCase, make_claude, read, tree_state, write

from atree import ops
from atree.agents import OpError, claude


class Move(HomeCase):
    def setUp(self):
        super().setUp()
        self.src = make_claude(self.home, self.project)
        self.companion = self.src[:-6]
        write(os.path.join(self.companion, "subagents", "agent.jsonl"), "companion\n")
        os.symlink("agent.jsonl", os.path.join(self.companion, "subagents", "alias"))
        [self.session] = claude.Claude().scan()
        self.original = read(self.src)
        self.contents = tree_state(self.companion)
        self.target = self.path("destination")
        os.makedirs(self.target)
        self.dst = os.path.join(claude.project_dir(self.target), os.path.basename(self.src))

    def assert_source_intact(self):
        self.assertEqual(read(self.src), self.original)
        self.assertEqual(tree_state(self.companion), self.contents)
        self.assertFalse(os.path.lexists(self.dst))
        self.assertFalse(os.path.lexists(self.dst[:-6]))

    def test_cross_filesystem_companion(self):
        with mock.patch.object(os, "rename", side_effect=OSError(errno.EXDEV, "cross-device link")):
            claude.Claude().move(self.session, self.target)
        self.assertFalse(os.path.exists(self.src))
        self.assertFalse(os.path.exists(self.companion))
        self.assertEqual(tree_state(self.dst[:-6]), self.contents)
        self.assertEqual(json.loads(read(self.dst))["cwd"], self.target)

    def test_same_filesystem_companion_is_renamed_with_readonly_contents(self):
        subdir = os.path.join(self.companion, "subagents")
        inode = os.stat(self.companion).st_ino
        os.chmod(subdir, 0o555)
        contents = tree_state(self.companion)

        def restore_permissions():
            # The backup preserves the read-only directory too.
            for root, dirs, files in os.walk(self.home):
                os.chmod(root, 0o755)

        self.addCleanup(restore_permissions)
        claude.Claude().move(self.session, self.target)
        self.assertFalse(os.path.exists(self.src))
        self.assertFalse(os.path.exists(self.companion))
        self.assertEqual(os.stat(self.dst[:-6]).st_ino, inode)
        self.assertEqual(tree_state(self.dst[:-6]), contents)

    def test_cross_filesystem_cleanup_failure_does_not_fail_move(self):
        rmtree = claude.shutil.rmtree

        def fail(path, *args, **kwargs):
            if path == self.companion:
                raise PermissionError(errno.EACCES, "cleanup denied")
            return rmtree(path, *args, **kwargs)

        with mock.patch.object(os, "rename", side_effect=OSError(errno.EXDEV, "cross-device link")), \
                mock.patch.object(claude.shutil, "rmtree", side_effect=fail):
            claude.Claude().move(self.session, self.target)
        self.assertFalse(os.path.exists(self.src))
        self.assertEqual(tree_state(self.dst[:-6]), self.contents)
        self.assertEqual(json.loads(read(self.dst))["cwd"], self.target)

    def test_companion_rename_failure_rolls_back_without_copy(self):
        with mock.patch.object(os, "rename", side_effect=PermissionError(errno.EACCES, "rename denied")):
            with self.assertRaisesRegex(PermissionError, "rename denied"):
                claude.Claude().move(self.session, self.target)
        self.assert_source_intact()

    def test_partial_companion_copy_failure(self):
        copytree = claude.shutil.copytree

        def fail(src, dst, *args, **kwargs):
            if dst == self.dst[:-6]:
                write(os.path.join(dst, "partial"), "partial copy")
                raise OSError("copy failed")
            return copytree(src, dst, *args, **kwargs)

        with mock.patch.object(os, "rename", side_effect=OSError(errno.EXDEV, "cross-device link")), \
                mock.patch.object(claude.shutil, "copytree", side_effect=fail):
            with self.assertRaisesRegex(OSError, "copy failed"):
                claude.Claude().move(self.session, self.target)
        self.assert_source_intact()

    def test_source_unlink_failure(self):
        self.check_source_unlink_failure()

    def test_cross_filesystem_source_unlink_failure(self):
        self.check_source_unlink_failure(OSError(errno.EXDEV, "cross-device link"))

    def check_source_unlink_failure(self, error=None):
        unlink = os.unlink

        def fail(path, *args, **kwargs):
            if path == self.src:
                raise OSError("unlink failed")
            return unlink(path, *args, **kwargs)

        with mock.patch.object(os, "rename", wraps=os.rename, side_effect=error), \
                mock.patch.object(os, "unlink", side_effect=fail):
            with self.assertRaisesRegex(OSError, "unlink failed"):
                claude.Claude().move(self.session, self.target)
        self.assert_source_intact()


class LivePaths(HomeCase):
    def test_glob_metacharacters_keep_running_sessions_protected(self):
        make_claude(self.home, self.project)
        for name in ("[work]", "work?", "work*"):
            with self.subTest(name=name):
                folder = self.path(name, "sessions")
                write(os.path.join(folder, "live.json"), json.dumps({
                    "sessionId": "claude-fixture", "pid": os.getpid(), "status": "waiting"}))
                write(self.path("work-other", "sessions", "decoy.json"), json.dumps({
                    "sessionId": "decoy", "pid": os.getpid()}))
                with mock.patch.object(claude, "CLAUDE_LIVE", folder):
                    self.assertEqual(claude.live_sessions(), {"claude-fixture": {"status": "waiting"}})
                    [session] = claude.Claude().scan()
                    self.assertTrue(session["live"])
                    self.assertEqual(session["live_status"], "waiting")
                    with self.assertRaisesRegex(OpError, "is running"):
                        ops.move(session["key"], self.home)
                    with self.assertRaisesRegex(OpError, "is running"):
                        ops.archive(session["key"])
