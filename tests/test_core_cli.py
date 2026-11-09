"""The assistant folder, the --op command line, AI search and atree.py's installers."""
import contextlib
import io
import json
import os
import shlex
import subprocess
import sys
import threading
import unittest
from unittest import mock

from support import ROOT, HomeCase, make_all, read

from atree import aisearch, assistant, ops
from atree.agents import OpError, base

sys.path.insert(0, ROOT)
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("atree_main", os.path.join(ROOT, "atree.py"))
atree_main = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(atree_main)

SIX = ["AGENTS.md", "CLAUDE.md", "GEMINI.md", "atree", "tasks.json", "tree.json"]


def run_cli(args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = assistant.cli(args)
    return code, out.getvalue()


class Assistant(HomeCase):
    def test_r_asst_1_concurrent(self):
        names = []
        real = os.symlink

        def spy(src, dst, *a, **k):
            names.append(dst)
            return real(src, dst, *a, **k)
        with mock.patch.object(os, "symlink", spy):
            ts = [threading.Thread(target=assistant.write_context, args=([],)) for _ in range(2)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertEqual(len(set(names)), len(names))
        d = assistant.AGENT_DIR
        self.assertEqual(os.readlink(os.path.join(d, "tasks.json")), ops.TASKS_FILE)
        self.assertEqual(json.loads(read(os.path.join(d, "tree.json")))["talks"], [])
        self.assertEqual(os.stat(os.path.join(d, "atree")).st_mode & 0o777, 0o755)
        self.assertEqual(sorted(os.listdir(d)), SIX)

    def test_r_asst_2_and_3_link(self):
        assistant.write_context([])
        link = os.path.join(assistant.AGENT_DIR, "tasks.json")
        ino = os.lstat(link).st_ino
        with mock.patch.object(os, "symlink") as sym:
            assistant.write_context([])
        sym.assert_not_called()
        self.assertEqual(os.lstat(link).st_ino, ino)
        os.unlink(link)
        os.symlink("/elsewhere", link)
        assistant.write_context([])
        self.assertEqual(os.readlink(link), ops.TASKS_FILE)

    def test_snapshot_and_status(self):
        make_all(self.home, self.project)
        own = base.talk("claude", "own", cwd=assistant.AGENT_DIR, title="mine", updated=1)
        ops._save(ops.TASKS_FILE, [{"id": "1", "text": "tidy", "done": True}])
        from atree.scan import scan_all
        assistant.write_context(scan_all() + [own])
        tree = json.loads(read(os.path.join(assistant.AGENT_DIR, "tree.json")))
        self.assertEqual(len(tree["talks"]), 5)
        self.assertNotIn("claude:own", {t["key"] for t in tree["talks"]})
        guide = read(os.path.join(assistant.AGENT_DIR, "CLAUDE.md"), "r")
        self.assertIn("./atree archive KEY...", guide)
        self.assertIn("5 talks (1 Claude, 1 Codex, 1 Gemini, 1 OpenCode, 1 Copilot) in 1 folders", guide)
        self.assertFalse(guide.endswith("\n"))  # G-core-16
        self.assertIn("- [x] tidy", guide)
        self.assertEqual(guide, read(os.path.join(assistant.AGENT_DIR, "AGENTS.md"), "r"))
        launcher = read(os.path.join(assistant.AGENT_DIR, "atree"), "r")
        self.assertTrue(launcher.startswith("#!/bin/sh\nexec "))
        self.assertTrue(launcher.endswith(' --op "$@"\n'))

    def test_open_terminal_continues_own(self):
        own = base.talk("claude", "own", cwd=assistant.AGENT_DIR)
        with mock.patch.object(ops, "run_in_terminal") as run:
            assistant.open_terminal("claude", [own])
            assistant.open_terminal("claude", [dict(own, archived=True)])
            assistant.open_terminal("codex", [])
        self.assertEqual([c.args[1] for c in run.call_args_list], ["claude --continue", "claude", "codex"])


class Cli(HomeCase):
    def test_usage_and_errors(self):
        self.assertEqual(run_cli([])[0], 2)
        self.assertEqual(run_cli(["task", " "]), (2, "usage: --op task TEXT\n"))
        self.assertEqual(run_cli(["move", "x"]), (2, "usage: --op move FOLDER KEY...\n"))
        self.assertEqual(run_cli(["frob", "a"]), (2, "unknown or empty op: frob a\n"))
        self.assertEqual(run_cli(["archive"]), (2, "unknown or empty op: archive\n"))
        self.assertEqual(run_cli(["archive", "nope"]), (1, "nope: not a talk key\n"))

    def test_task(self):
        self.assertEqual(run_cli(["task", "fix", "  the\tthing"]), (0, "task added\n"))
        [t] = json.loads(read(ops.TASKS_FILE))
        self.assertEqual((t["text"], t["done"]), ("fix the thing", False))

    def test_ops(self):
        make_all(self.home, self.project)
        code, out = run_cli(["archive", "claude:claude-fixture", "claude:missing"])
        self.assertEqual(code, 1)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("archive claude:claude-fixture: " + ops.ARCHIVE_DIR))
        self.assertEqual(lines[1], "archive claude:missing: FAILED session claude:missing not found")
        code, out = run_cli(["restore", "claude:claude-fixture"])
        self.assertEqual(code, 0)
        code, out = run_cli(["move", self.project, "claude:claude-fixture"])
        self.assertEqual(out, "move claude:claude-fixture: unchanged\n")

    def test_tree(self):
        self.assertEqual(run_cli(["tree"]), (0, "wrote %s\n" % os.path.join(assistant.AGENT_DIR, "tree.json")))


class AiSearch(HomeCase):
    def sessions(self):
        return [base.talk("claude", "a", title="Fix login", cwd=self.project),
                base.talk("codex", "b", title="Old", archived=True)]

    def fake(self, out="", err="", code=0):
        proc = mock.Mock(pid=999999, returncode=code)
        proc.communicate.return_value = (out, err)
        return mock.patch.object(aisearch.subprocess, "Popen", return_value=proc)

    def test_results(self):
        with self.fake('noise [1, 2] [{"key": "claude:a", "why": "login"}, {"key": "codex:b"}, {"key": "claude:a"}]') as p:
            got = aisearch.search("claude", "auth bug", self.sessions())
        self.assertEqual(got, [{"key": "claude:a", "why": "login"}])
        prompt = p.return_value.communicate.call_args.args[0]
        self.assertIn("auth bug", prompt)
        self.assertIn("claude:a | claude | ~/project", prompt)
        self.assertNotIn("codex:b", prompt)
        self.assertEqual(p.call_args.args[0][0], "claude")
        self.assertTrue(p.call_args.kwargs["start_new_session"])

    def test_prompt_layout(self):
        # G-core-15: query inline in the request sentence, blank-line separated, no trailing newline
        lines = aisearch.build_prompt("  auth bug ", self.sessions()[:1]).split("\n")
        self.assertIn("user's", lines[0])
        self.assertEqual(lines[1], "key | agent | folder | last active | title | first prompt")
        self.assertEqual((lines[2], lines[4], lines[6]), ("", "", ""))
        self.assertTrue(lines[3].endswith(": auth bug"), lines[3])
        self.assertIn("JSON array", lines[5])
        self.assertTrue(lines[7].startswith("claude:a | claude | ~/project | "), lines[7])
        self.assertEqual(len(lines), 8)

    def test_errors(self):
        with self.assertRaisesRegex(OpError, "OpenCode can't answer questions headless"):
            aisearch.search("opencode", "x", [])
        with self.fake("[]", "boom\nlast words", 3):
            with self.assertRaisesRegex(OpError, "Claude failed: last words"):
                aisearch.search("claude", "x", [])
        with self.fake("no list here"):
            with self.assertRaisesRegex(OpError, "didn't answer with a list"):
                aisearch.search("claude", "x", [])
        with mock.patch.object(aisearch.subprocess, "Popen", side_effect=FileNotFoundError):
            with self.assertRaisesRegex(OpError, "claude not found"):
                aisearch.search("claude", "x", [])

    def test_timeout_kills_group(self):
        proc = mock.Mock(pid=12345)
        proc.communicate.side_effect = [subprocess.TimeoutExpired("x", 1), ("", "")]
        with mock.patch.object(aisearch.subprocess, "Popen", return_value=proc), \
                mock.patch.object(aisearch.os, "killpg") as kill:
            with self.assertRaisesRegex(OpError, "took longer than 180s"):
                aisearch.search("codex", "x", [])
        kill.assert_called_once_with(12345, 9)


class Main(HomeCase):
    def main(self, *args):
        with mock.patch.object(sys, "argv", ["atree.py", *args]):
            try:
                atree_main.main()
            except SystemExit as e:
                return e.code
        return None

    def test_r_cli_1_installers(self):
        for flag, name in (("--install-desktop", "install_desktop"), ("--install-extension", "install_extension"),
                           ("--install-statusline", "install_statusline")):
            with mock.patch.object(atree_main, name) as fn:
                self.main(flag)
            fn.assert_called_once_with()
        with mock.patch("atree.statusline.uninstall") as un:
            self.main("--uninstall-statusline")
        un.assert_called_once_with()

    def test_r_cli_2_statusline(self):
        with mock.patch("atree.statusline.run", return_value=7):
            self.assertEqual(self.main("--statusline"), 7)

    def test_r_cli_3_op_wins(self):
        with mock.patch("atree.assistant.cli", return_value=3) as cli:
            self.assertEqual(self.main("--op", "task", "check --install-desktop", "--install-statusline"), 3)
        cli.assert_called_once_with(["task", "check --install-desktop", "--install-statusline"])

    def test_r_cli_4_gui(self):
        gui = mock.Mock(return_value=7)
        with mock.patch.dict(sys.modules, {"atree.qt.app": mock.Mock(main=gui)}):
            self.assertEqual(self.main(), 7)
            self.assertEqual(self.main("--widget"), 7)
        self.assertEqual([c.kwargs for c in gui.call_args_list], [{"widget": False}, {"widget": True}])

    def test_r_cli_5_no_toolkit(self):
        with mock.patch.dict(sys.modules, {"PySide6": None}), \
                mock.patch.object(atree_main, "VENV_PY", atree_main.Path(self.path("no-venv"))), \
                mock.patch.object(os, "execv") as execv:
            msg = self.main()
        execv.assert_not_called()
        self.assertIn("python3 -m venv .venv && .venv/bin/python -m pip install PySide6-Essentials", msg)

    def install_env(self, here, data_home):
        ext = os.path.join(here, "gnome-extension", "atree@atree")
        os.makedirs(ext, exist_ok=True)
        env = {"HOME": self.home}
        if data_home is not None:
            env["XDG_DATA_HOME"] = data_home
        return ext, [mock.patch.object(atree_main, "HERE", atree_main.Path(here)),
                     mock.patch.object(atree_main, "VENV_PY", atree_main.Path(here, ".venv", "bin", "python")),
                     mock.patch.object(atree_main, "ICON", atree_main.Path(here, "icon.svg")),
                     mock.patch.dict(os.environ, env, clear=True)]

    def test_r_install_1(self):
        for case in ("unset", "empty", "custom"):
            with self.subTest(case):
                here = self.path("app-" + case)
                venv = os.path.join(here, ".venv", "bin", "python")
                os.makedirs(os.path.dirname(venv))
                open(venv, "w").close()
                data_home = {"unset": None, "empty": "", "custom": self.path("custom data")}[case]
                effective = data_home or os.path.join(self.home, ".local", "share")
                ext, patches = self.install_env(here, data_home)
                with contextlib.ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)
                    run = stack.enter_context(mock.patch.object(atree_main.subprocess, "run",
                                                                return_value=mock.Mock(returncode=0)))
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    atree_main.install_desktop()
                    atree_main.install_extension()
                self.assertIn("Name=atree", read(os.path.join(effective, "applications", "atree.desktop"), "r"))
                link = os.path.join(effective, "gnome-shell", "extensions", "atree@atree")
                self.assertEqual(os.path.realpath(link), os.path.realpath(ext))
                self.assertFalse(os.path.exists(os.path.join(ext, "config.json")))
                self.assertEqual(json.loads(read(os.path.join(effective, "atree", "extension.json"))),
                                 {"python": venv, "app": os.path.join(here, "atree.py")})
                self.assertEqual(run.call_args.args[0], ["gnome-extensions", "enable", "atree@atree"])
                self.assertEqual(run.call_count, 1)
                if case == "custom":
                    self.assertFalse(os.path.exists(os.path.join(self.home, ".local", "share")))
                import shutil
                shutil.rmtree(os.path.join(self.home, ".local"), ignore_errors=True)

    def test_r_install_2_no_venv(self):
        here = self.path("app")
        _ext, patches = self.install_env(here, self.path("d"))
        with contextlib.ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            stack.enter_context(mock.patch.object(atree_main.subprocess, "run", return_value=mock.Mock(returncode=0)))
            inst = stack.enter_context(mock.patch("atree.statusline.install"))
            atree_main.install_desktop()
            atree_main.install_extension()
            atree_main.install_statusline()
        exec_line = [l for l in read(self.path("d", "applications", "atree.desktop"), "r").splitlines()
                     if l.startswith("Exec=")][0]
        self.assertEqual(shlex.split(exec_line[5:])[0], sys.executable)
        self.assertEqual(json.loads(read(self.path("d", "atree", "extension.json")))["python"], sys.executable)
        inst.assert_called_once_with("%s %s --statusline" % (shlex.quote(sys.executable),
                                                             shlex.quote(os.path.join(here, "atree.py"))))

    GLIB_PARSE = (
        "import json, sys\n"
        "from gi.repository import GLib\n"
        "kf = GLib.KeyFile(); kf.load_from_file(sys.argv[1], GLib.KeyFileFlags.NONE)\n"
        "print(json.dumps([GLib.shell_parse_argv(kf.get_string('Desktop Entry', 'Exec'))[1],"
        " kf.get_string('Desktop Entry', 'Icon')]))\n")

    @unittest.skipUnless(os.path.exists("/usr/bin/python3") and subprocess.run(
        ["/usr/bin/python3", "-c", "import gi"], capture_output=True).returncode == 0, "needs system GLib")
    def test_r_install_3_exec_quoting(self):
        for name in ("plain", "space #?&日本語", 'quote" dollar$ tick` slash\\', "nl\ncr\rtab\t", "literal%f%u%%"):
            with self.subTest(name=name):
                here = self.path(name)
                _ext, patches = self.install_env(here, self.path("d"))
                with contextlib.ExitStack() as stack:
                    for p in patches:
                        stack.enter_context(p)
                    stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                    atree_main.install_desktop()
                out = subprocess.run(["/usr/bin/python3", "-c", self.GLIB_PARSE,
                                      self.path("d", "applications", "atree.desktop")],
                                     capture_output=True, text=True, check=True).stdout
                argv, icon = json.loads(out)
                self.assertEqual(argv, [sys.executable.replace("%", "%%"),
                                        os.path.join(here, "atree.py").replace("%", "%%")])
                self.assertEqual(icon, os.path.join(here, "icon.svg"))

if __name__ == "__main__":
    unittest.main()
