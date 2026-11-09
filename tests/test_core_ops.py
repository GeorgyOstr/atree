"""Archive, restore and delete: verified packing, safe extraction, full rollback."""
import io
import json
import os
import tarfile
import unittest
from unittest import mock

from support import HomeCase, make_all, read, tree_state, write

from atree import ops, reattach, scan
from atree.agents import Agent, OpError, base, codex


class Stub(Agent):
    name = "stub"
    label = "Stub"

    def __init__(self, base_dir):
        self.base_dir = base_dir
        self.after_archive = mock.Mock()
        self.after_restore = mock.Mock()

    def archive_members(self, s):
        return self.base_dir, [os.path.join(self.base_dir, "session")]


class StubCase(HomeCase):
    def setUp(self):
        super().setUp()
        self.base = self.path("store")
        self.session = os.path.join(self.base, "session")
        self.transcript = write(os.path.join(self.session, "transcript.jsonl"),
                                "".join('{"line":%d}\n' % i for i in range(64)))
        self.agent = Stub(self.base)
        self.talk = base.talk("stub", "s1", path=self.transcript, size=10)
        for name, value in (("_find", lambda key: dict(self.talk)), ("_agent", lambda name: self.agent)):
            p = mock.patch.object(ops, name, value)
            p.start()
            self.addCleanup(p.stop)
        os.makedirs(ops.ARCHIVE_DIR)
        self.old_archive = write(os.path.join(ops.ARCHIVE_DIR, "older.tar.gz"), b"unrelated")
        ops._save(ops.ARCHIVE_INDEX, {})
        self.index_bytes = read(ops.ARCHIVE_INDEX)

    def after_fsync(self, action):
        """Run ``action(path of the temporary)`` right after the archive's fsync."""
        real = os.fsync
        calls = []

        def spy(fd):
            real(fd)
            calls.append(fd)
            action(os.readlink("/proc/self/fd/%d" % fd))
        p = mock.patch.object(os, "fsync", spy)
        p.start()
        self.addCleanup(p.stop)
        return calls

    def assert_untouched(self, calls, archives=("older.tar.gz",)):
        self.assertEqual(len(calls), 1)
        self.assertEqual(read(ops.ARCHIVE_INDEX), self.index_bytes)
        self.assertEqual(sorted(os.listdir(ops.ARCHIVE_DIR)), sorted(list(archives) + ["index.json"]))
        self.assertEqual(read(self.old_archive), b"unrelated")
        self.agent.after_archive.assert_not_called()


class Archive(StubCase):
    def test_r_archive_1_footer_corruption(self):
        original = read(self.transcript)

        def flip(tmp):
            with open(tmp, "r+b") as f:
                f.seek(-8, 2)
                b = f.read(1)
                f.seek(-8, 2)
                f.write(bytes([b[0] ^ 1]))
        calls = self.after_fsync(flip)
        with self.assertRaises(OpError):
            ops.archive("stub:s1")
        self.assertEqual(read(self.transcript), original)
        self.assert_untouched(calls)

    def test_r_archive_2_damaged_streams(self):
        payloads = {"magic": b"PK\x03\x04" + b"\0" * 40, "header": b"\x1f\x8b\x08\x00\x00",
                    "deflate": None, "block": b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x03\x07\x00"}
        for name, data in payloads.items():
            with self.subTest(name):
                def replace(tmp, data=data):
                    if data is None:
                        data = read(tmp)[:15]
                    with open(tmp, "wb") as f:
                        f.write(data)
                with mock.patch.object(os, "fsync", side_effect=lambda fd, d=data: replace(os.readlink("/proc/self/fd/%d" % fd))):
                    with self.assertRaises(OpError):
                        ops.archive("stub:s1")
                self.assertTrue(os.path.exists(self.transcript))
                self.assertEqual(read(ops.ARCHIVE_INDEX), self.index_bytes)
                self.assertEqual(sorted(os.listdir(ops.ARCHIVE_DIR)), ["index.json", "older.tar.gz"])

    def test_r_archive_3_same_size_rewrite(self):
        st = os.stat(self.transcript)

        def change(_tmp):
            with open(self.transcript, "r+b") as f:
                f.write(b"X")
            os.utime(self.transcript, ns=(st.st_atime_ns, st.st_mtime_ns))
        calls = self.after_fsync(change)
        with self.assertRaisesRegex(OpError, "doesn't match its files"):
            ops.archive("stub:s1")
        self.assertTrue(read(self.transcript).startswith(b"X"))
        self.assert_untouched(calls)

    def test_r_archive_4_atomic_replacement(self):
        self.agent.archive_members = lambda s: (self.session, [self.transcript])
        st = os.stat(self.transcript)

        def swap(_tmp):
            new = write(self.transcript + ".new", b"Y" * st.st_size, mtime=None)
            os.utime(new, ns=(st.st_atime_ns, st.st_mtime_ns))
            os.replace(new, self.transcript)
        calls = self.after_fsync(swap)
        with self.assertRaises(OpError):
            ops.archive("stub:s1")
        self.assertTrue(read(self.transcript).startswith(b"YYY"))
        self.assert_untouched(calls)

    def test_r_archive_5_new_directory(self):
        calls = self.after_fsync(lambda _tmp: os.mkdir(os.path.join(self.session, "fresh")))
        with self.assertRaises(OpError):
            ops.archive("stub:s1")
        self.assertTrue(os.path.isdir(os.path.join(self.session, "fresh")))
        self.assert_untouched(calls)

    def test_r_archive_6_symlink_member(self):
        target = self.path("target")
        os.makedirs(target)
        os.symlink(target, os.path.join(self.session, "linked"))
        before = tree_state(self.home)
        with self.assertRaisesRegex(OpError, "unsafe archive member: session/linked"):
            ops.archive("stub:s1")
        after = tree_state(self.home)
        after.pop(os.path.relpath(os.path.join(ops.DATA, "ops.lock"), self.home), None)
        self.assertEqual(after, before)
        self.agent.after_archive.assert_not_called()

    def test_r_archive_7_round_trip(self):
        write(os.path.join(self.session, "nested", "transcript"), "deep")
        out = ops.archive("stub:s1")
        self.assertFalse(os.path.exists(self.session))
        self.assertTrue(out.endswith("stub-s1.tar.gz"))
        self.agent.after_archive.assert_called_once()
        self.assertEqual(ops.restore("stub:s1"), self.transcript)
        self.assertEqual(read(os.path.join(self.session, "nested", "transcript"), "r"), "deep")
        self.assertEqual(ops.archived(), {})
        self.assertFalse(os.path.exists(out))

    def test_after_archive_failure_rolls_back(self):
        self.agent.after_archive.side_effect = RuntimeError("db locked")
        with self.assertRaisesRegex(RuntimeError, "db locked"):
            ops.archive("stub:s1")
        self.assertTrue(os.path.exists(self.transcript))
        self.assertEqual(ops.archived(), {})
        self.assertEqual(sorted(os.listdir(ops.ARCHIVE_DIR)), ["index.json", "older.tar.gz"])

    def test_name_collision_and_double_archive(self):
        write(os.path.join(ops.ARCHIVE_DIR, "stub-s1.tar.gz"), b"older copy")
        out = ops.archive("stub:s1")
        self.assertTrue(out.endswith("stub-s1-2.tar.gz"))
        with self.assertRaisesRegex(OpError, "already archived"):
            ops.archive("stub:s1")

    def test_refusals(self):
        self.talk["live"] = True
        with self.assertRaisesRegex(OpError, "is running; close it before archiving"):
            ops.archive("stub:s1")
        self.agent.can_archive = False
        with self.assertRaisesRegex(OpError, "archiving Stub talks isn't supported"):
            ops.archive("stub:s1")


class Restore(StubCase):
    def build(self, members, gz=True):
        """Write an archive with ``members`` [(name, type, data)] and index it."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz" if gz else "w") as tar:
            for name, kind, data in members:
                info = tarfile.TarInfo(name)
                if kind == "dir":
                    info.type = tarfile.DIRTYPE
                    tar.addfile(info)
                elif kind in ("sym", "hard"):
                    info.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                    info.linkname = data
                    tar.addfile(info)
                else:
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
        path = os.path.join(ops.ARCHIVE_DIR, "stub-s1.tar.gz")
        write(path, buf.getvalue())
        self.restore_base = self.path("restore-base")
        os.makedirs(self.restore_base, exist_ok=True)
        ops._save(ops.ARCHIVE_INDEX, {"stub:s1": dict(self.talk, archive=path, base=self.restore_base)})
        self.index_bytes = read(ops.ARCHIVE_INDEX)
        return path

    def assert_refused(self, path, regex=""):
        archive_bytes = read(path)
        with mock.patch.object(tarfile.TarFile, "extract", wraps=None) as extract:
            with self.assertRaisesRegex(OpError, regex):
                ops.restore("stub:s1")
        extract.assert_not_called()
        self.assertEqual(read(path), archive_bytes)
        self.assertEqual(read(ops.ARCHIVE_INDEX), self.index_bytes)
        self.agent.after_restore.assert_not_called()

    def test_r_restore_1_damaged(self):
        good = [("transcript", "file", b"payload " * 64)]
        for damage in ("magic", "header", "deflate", "footer-8", "footer-4", "payload", "truncated"):
            with self.subTest(damage):
                path = self.build(good)
                data = bytearray(read(path))
                if damage == "magic":
                    data[:4] = b"PK\x03\x04"
                elif damage == "header":
                    data = data[:5]
                elif damage == "deflate":
                    data = data[:15]
                elif damage.startswith("footer"):
                    data[-int(damage[-1])] ^= 1
                elif damage == "payload":
                    data[len(data) // 2] ^= 0x55
                else:
                    data = data[:-4]
                write(path, bytes(data))
                self.assert_refused(path)
                self.assertEqual(os.listdir(self.restore_base), [])

    def test_r_restore_2_escaping_names(self):
        victim = write(self.path("victim"), "keep")
        outside = write(self.path("outside"), "keep")
        for bad in ("missing/../victim", "../outside", victim, "."):
            with self.subTest(bad):
                path = self.build([("new", "file", b"x"), (bad, "file", b"evil")])
                self.assert_refused(path, "unsafe archive member")
                self.assertEqual(read(victim, "r") + read(outside, "r"), "keepkeep")
                self.assertFalse(os.path.exists(os.path.join(self.restore_base, "new")))

    def test_r_restore_3_existing_destinations(self):
        for name in ("victim", "dangling"):
            with self.subTest(name):
                path = self.build([(name, "file", b"evil")])
                if name == "victim":
                    write(os.path.join(self.restore_base, "victim"), "keep")
                else:
                    os.symlink(self.path("nowhere"), os.path.join(self.restore_base, "dangling"))
                self.assert_refused(path, "already exists")
        self.assertEqual(read(os.path.join(self.restore_base, "victim"), "r"), "keep")
        self.assertTrue(os.path.islink(os.path.join(self.restore_base, "dangling")))

    def test_r_restore_4_rollback(self):
        path = self.build([("new", "dir", None), ("new/transcript", "file", b"x"), ("victim/transcript", "file", b"y")])
        write(os.path.join(self.restore_base, "victim"), "keep")
        with self.assertRaises(OpError):
            ops.restore("stub:s1")
        self.assertEqual(read(os.path.join(self.restore_base, "victim"), "r"), "keep")
        self.assertFalse(os.path.exists(os.path.join(self.restore_base, "new")))
        self.assertTrue(os.path.exists(path))
        self.agent.after_restore.assert_not_called()

    def test_r_restore_5_links(self):
        for kind in ("sym", "hard"):
            with self.subTest(kind):
                path = self.build([("transcript", "file", b"x"), ("alias", kind, "transcript")])
                self.assert_refused(path, "unsafe archive member: alias")
                self.assertEqual(os.listdir(self.restore_base), [])

    def test_not_archived(self):
        with self.assertRaisesRegex(OpError, "stub:nope is not archived"):
            ops.restore("stub:nope")
        with self.assertRaisesRegex(OpError, "stub:nope is not archived"):
            ops.delete_archived("stub:nope")


class RealAgents(HomeCase):
    def test_r_archive_8(self):
        make_all(self.home, self.project)
        os.makedirs(codex.CODEX_DIR, exist_ok=True)
        for key in ("claude:claude-fixture", "codex:codex-fixture", "gemini:gemini-fixture",
                    "copilot:copilot-fixture"):
            with self.subTest(key):
                ops.archive(key)
                self.assertNotIn(key, {t["key"] for t in scan.scan_all()})
                ops.restore(key)
                self.assertIn(key, {t["key"] for t in scan.scan_all()})
                path = ops.archive(key)
                self.assertEqual(ops.delete_archived(key), path)
                self.assertNotIn(key, ops.archived())
                self.assertFalse(os.path.exists(path))
        with self.assertRaisesRegex(OpError, "archiving OpenCode talks isn't supported"):
            ops.archive("opencode:opencode-fixture")


class Resume(HomeCase):
    def test_resume_command(self):
        make_all(self.home, self.project)
        self.assertEqual(ops.resume_command("claude:claude-fixture"), (self.project, "claude --resume claude-fixture"))
        self.assertEqual(ops.resume_command("copilot:copilot-fixture")[1], "copilot --resume=copilot-fixture")
        with mock.patch.object(ops, "_find", return_value=base.talk("claude", "x; rm -rf ~")):
            with self.assertRaisesRegex(OpError, "unexpected characters"):
                ops.resume_command("claude:x; rm -rf ~")

    def test_run_in_terminal(self):
        with self.assertRaisesRegex(OpError, "no longer exists"):
            ops.run_in_terminal(self.path("gone"), "true")
        launched = []
        with mock.patch.object(ops, "_gsetting", side_effect=["ptyxis", ""]), \
                mock.patch.object(ops.shutil, "which", side_effect=lambda p: "/usr/bin/" + p), \
                mock.patch.object(ops.subprocess, "Popen", side_effect=lambda argv, **kw: launched.append((argv, kw)) or mock.Mock()), \
                mock.patch.dict(os.environ, {"SHELL": "/bin/zsh"}):
            ops.run_in_terminal(self.project, "claude --resume a")
        argv, kw = launched[0]
        self.assertEqual(argv, ["ptyxis", "--new-window", "--working-directory", self.project, "--",
                                "/bin/zsh", "-i", "-c", "claude --resume a; exec /bin/zsh"])
        self.assertEqual(kw["cwd"], self.project)
        with mock.patch.object(ops, "_gsetting", return_value=None), \
                mock.patch.object(ops.shutil, "which", return_value=None):
            with self.assertRaisesRegex(OpError, "no terminal emulator found"):
                ops.run_in_terminal(self.project, "true")

    def test_open_terminal_resumes_a_talk_that_is_not_running(self):
        make_all(self.home, self.project)
        with mock.patch.object(ops, "run_in_terminal") as run, mock.patch.object(reattach, "reattach") as re:
            self.assertEqual(ops.open_terminal("claude:claude-fixture"), "opening terminal…")
        run.assert_called_once_with(self.project, "claude --resume claude-fixture")
        re.assert_not_called()

    def test_open_terminal_shows_a_running_talk_instead(self):
        make_all(self.home, self.project)
        write(self.path(".claude", "sessions", "1.json"),
              json.dumps({"sessionId": "claude-fixture", "pid": os.getpid()}))
        with mock.patch.object(ops, "run_in_terminal") as run, \
                mock.patch.object(reattach, "reattach", return_value="raised its terminal window") as re:
            self.assertEqual(ops.open_terminal("claude:claude-fixture"), "raised its terminal window")
            self.assertEqual(re.call_args[0][:2], (os.getpid(), "hello claude"))
            run.assert_not_called()
            re.call_args[0][2]("tmux attach-session -t '$1'")  # attaching opens a terminal in the talk's folder
            run.assert_called_once_with(self.project, "tmux attach-session -t '$1'")
        # the process ended meanwhile: resume as usual
        with mock.patch.object(ops, "run_in_terminal") as run2, mock.patch.object(reattach, "reattach", return_value=None):
            self.assertEqual(ops.open_terminal("claude:claude-fixture"), "opening terminal…")
        run2.assert_called_once_with(self.project, "claude --resume claude-fixture")


class FakeProc(HomeCase):
    """A /proc tree: agent 100 under shell 90 under ``top`` (pid 80), under systemd (pid 1)."""

    def setUp(self):
        super().setUp()
        self.proc = self.path("proc")
        p = mock.patch.object(base, "PROC", self.proc)
        p.start()
        self.addCleanup(p.stop)
        self.run = mock.Mock()

    def proc_entry(self, pid, comm, ppid, env=None, cmdline=("x",)):
        write(os.path.join(self.proc, str(pid), "stat"), "%d (%s) S %d 1 1 0" % (pid, comm, ppid))
        write(os.path.join(self.proc, str(pid), "environ"), "".join("%s=%s\0" % kv for kv in (env or {}).items()))
        write(os.path.join(self.proc, str(pid), "cmdline"), "\0".join(cmdline) + "\0")

    def tree(self, top, env=None):
        self.proc_entry(100, "claude", 90, env)
        self.proc_entry(90, "fish", 80)
        self.proc_entry(80, top, 1)

    def test_ancestors_stop_before_init_and_on_loops(self):
        self.tree("terminator")
        self.assertEqual(reattach.ancestors(100), [(90, "fish"), (80, "terminator")])
        self.proc_entry(7, "a) b", 8)
        self.proc_entry(8, "loop", 7)
        self.assertEqual(reattach.ancestors(7), [(8, "loop")])
        self.assertEqual(reattach.ancestors(12345), [])

    def test_terminal_window_is_raised(self):
        self.tree("terminator")
        with mock.patch.object(reattach, "focus_window", return_value=True) as focus:
            self.assertEqual(reattach.reattach(100, "t", self.run), "raised its terminal window")
        focus.assert_called_once_with([100, 90, 80])
        self.run.assert_not_called()

    def test_no_window_or_no_way_to_raise_it_is_an_error_not_a_second_resume(self):
        self.tree("sshd")
        with mock.patch.object(reattach, "focus_window", return_value=False):
            with self.assertRaisesRegex(OpError, "'t' is already running \\(pid 100\\) without a terminal window"):
                reattach.reattach(100, "t", self.run)
        with mock.patch.object(reattach, "focus_window", return_value=None):
            with self.assertRaisesRegex(OpError, "install-extension"):
                reattach.reattach(100, "t", self.run)
        self.run.assert_not_called()

    def test_gone_process_means_resume(self):
        with mock.patch.object(base, "pid_alive", return_value=False):
            self.assertIsNone(reattach.reattach(100, "t", self.run))

    def tmux(self, panes, clients=""):
        calls = []

        def fake(argv, timeout=3):
            calls.append(argv)
            if "list-panes" in argv:
                return panes
            if "list-clients" in argv:
                return clients
            return ""
        return calls, mock.patch.object(reattach, "_run", side_effect=fake)

    def test_detached_tmux_is_attached_in_a_new_terminal(self):
        self.tree("tmux: server", env={"TMUX": "/run/tmux sock,80,1"})
        calls, p = self.tmux("55 %1 $0 0\n90 %3 $1 0\n")
        with p:
            self.assertEqual(reattach.reattach(100, "t", self.run), "attaching to its tmux session…")
        self.run.assert_called_once_with("tmux -S '/run/tmux sock' attach-session -t '$1'")
        self.assertIn(["tmux", "-S", "/run/tmux sock", "select-window", "-t", "%3"], calls)
        self.assertIn(["tmux", "-S", "/run/tmux sock", "select-pane", "-t", "%3"], calls)

    def test_attached_tmux_raises_its_client_window(self):
        self.tree("tmux: server", env={"TMUX": "/s,80,1"})
        self.proc_entry(70, "tmux: client", 60)
        self.proc_entry(60, "ptyxis", 1)
        _, p = self.tmux("90 %3 $1 1\n", clients="70\n")
        with p, mock.patch.object(reattach, "focus_window", return_value=True) as focus:
            self.assertEqual(reattach.reattach(100, "t", self.run), "raised its tmux window")
        focus.assert_called_once_with([70, 60])
        self.run.assert_not_called()
        # attached over ssh: no window, so another client attaches
        _, p = self.tmux("90 %3 $1 1\n", clients="70\n")
        with p, mock.patch.object(reattach, "focus_window", return_value=False):
            reattach.reattach(100, "t", self.run)
        self.run.assert_called_once_with("tmux -S /s attach-session -t '$1'")

    def test_tmux_socket_found_without_tmux_env(self):
        self.tree("tmux: server")
        os.makedirs(os.path.join(self.proc, "80", "fd"))
        os.symlink("socket:[4242]", os.path.join(self.proc, "80", "fd", "3"))
        write(os.path.join(self.proc, "net", "unix"),
              "Num RefCount Protocol Flags Type St Inode Path\n"
              "0: 00000002 00000000 00010000 0001 01 1111 /tmp/other\n"
              "0: 00000002 00000000 00010000 0001 01 4242 /tmp/tmux-1000/default\n")
        _, p = self.tmux("90 %3 $1 0\n")
        with p:
            reattach.reattach(100, "t", self.run)
        self.run.assert_called_once_with("tmux -S /tmp/tmux-1000/default attach-session -t '$1'")
        _, p = self.tmux("")
        with p, self.assertRaisesRegex(OpError, "can't reach that tmux server"):
            reattach.reattach(100, "t", self.run)

    def test_screen_session_is_attached(self):
        self.tree("screen", env={"STY": "80.work"})
        self.assertEqual(reattach.reattach(100, "t", self.run), "attaching to its screen session…")
        self.run.assert_called_once_with("screen -x 80.work")

    def test_session_pid_by_argument_or_open_transcript(self):
        self.proc_entry(200, "codex", 1, cmdline=("codex", "resume", "abc-1"))
        self.proc_entry(201, "copilot", 1, cmdline=("copilot", "--resume=abc-2"))
        self.proc_entry(202, "sh", 1, cmdline=("sh", "-c", "codex resume abc-3; exec sh"))
        self.proc_entry(203, "gemini", 1, cmdline=("gemini",))
        os.makedirs(os.path.join(self.proc, "203", "fd"))
        os.symlink("/t/abc-3.json", os.path.join(self.proc, "203", "fd", "9"))
        self.assertEqual(base.session_pid({"id": "abc-1"}), 200)
        self.assertEqual(base.session_pid({"id": "abc-2"}), 201)
        self.assertEqual(base.session_pid({"id": "abc-3", "path": "/t/abc-3.json"}), 203)
        self.assertIsNone(base.session_pid({"id": "abc-3", "path": None}))


class FocusWindow(unittest.TestCase):
    def test_gnome_extension_first(self):
        with mock.patch.object(reattach, "_run", return_value="(true,)\n") as run:
            self.assertIs(reattach.focus_window([5, os.getpid(), 6]), True)
        argv = run.call_args[0][0]
        self.assertEqual(argv[:2], ["gdbus", "call"])
        self.assertIn("org.atree.Desktop.Activate", argv)
        self.assertEqual(argv[-1], "@au [5, 6]")  # never atree's own window
        with mock.patch.object(reattach, "_run", return_value="(false,)\n"):
            self.assertIs(reattach.focus_window([5]), False)

    def test_x11_tools_only_outside_wayland(self):
        with mock.patch.object(reattach, "_run", return_value=None), \
                mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"}):
            self.assertIsNone(reattach.focus_window([5]))
        out = {"wmctrl -lp": "0x01  0 7 host other\n0x02  0 6 host term one\n0x03 -1 6 host term two\n"}
        calls = []

        def fake(argv, timeout=3):
            calls.append(argv)
            return None if argv[0] == "gdbus" else out.get(" ".join(argv), "")
        with mock.patch.object(reattach, "_run", side_effect=fake), \
                mock.patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"}):
            self.assertIs(reattach.focus_window([5, 6, 7]), True)
            self.assertEqual(calls[-1], ["wmctrl", "-i", "-a", "0x02"])
            self.assertIs(reattach.focus_window([9]), False)


class TerminalEnv(HomeCase):
    def test_terminator_behind_x_terminal_emulator_gets_one_string(self):
        launched = []
        real = {"x-terminal-emulator": "/usr/bin/terminator"}
        with mock.patch.object(ops, "_gsetting", side_effect=["x-terminal-emulator", ""]), \
                mock.patch.object(ops.shutil, "which", side_effect=lambda p: "/usr/bin/" + p), \
                mock.patch.object(ops.os.path, "realpath", side_effect=lambda p: real.get(os.path.basename(p), p)), \
                mock.patch.object(ops.subprocess, "Popen", side_effect=lambda argv, **kw: launched.append(argv) or mock.Mock()), \
                mock.patch.dict(os.environ, {"SHELL": "/usr/bin/zsh"}):
            ops.run_in_terminal(self.project, "claude --resume abc")
        argv = launched[0]
        self.assertEqual(argv[:3], ["terminator", "--working-directory=" + self.project, "--command"])
        self.assertEqual(argv[3], "/usr/bin/zsh -i -c 'claude --resume abc; exec /usr/bin/zsh'")

    def test_adopt_shell_path_adds_missing_entries_first(self):
        with mock.patch.object(ops, "shell_path", return_value="/home/u/.local/bin:/usr/bin"), \
                mock.patch.dict(os.environ, {"PATH": "/usr/bin:/bin"}):
            ops.adopt_shell_path()
            self.assertEqual(os.environ["PATH"], "/home/u/.local/bin:/usr/bin:/bin")
        with mock.patch.object(ops, "shell_path", return_value=None), \
                mock.patch.dict(os.environ, {"PATH": "/usr/bin"}):
            ops.adopt_shell_path()
            self.assertEqual(os.environ["PATH"], "/usr/bin")

    def test_shell_path_reads_the_interactive_shell(self):
        rc = os.path.join(self.home, "rc.sh")
        with open(rc, "w") as f:
            f.write('echo noise from rc\nPATH="/opt/agent/bin:$PATH"\n')
        with mock.patch.dict(os.environ, {"SHELL": "/bin/sh", "ENV": rc, "PATH": "/usr/bin:/bin"}):
            self.assertTrue(ops.shell_path().startswith("/opt/agent/bin:"))


if __name__ == "__main__":
    unittest.main()
