"""Shared helpers: pid probing, parse caches, atomic writes and transcript rewrites."""
import json
import os
import threading
import unittest
from unittest import mock

from support import HomeCase, read, write

from atree import fsutil
from atree.agents import base


def numbers():
    calls = []

    def init():
        return []

    def step(state, line):
        calls.append(line)
        state.append(json.loads(line)["n"])

    def finish(state):
        return list(state)

    return calls, init, step, finish


class Helpers(unittest.TestCase):
    def test_tildify(self):
        with mock.patch.object(base, "HOME", "/home/user"):
            self.assertEqual(base.tildify("/home/user"), "~")
            self.assertEqual(base.tildify("/home/user/x"), "~/x")
            self.assertEqual(base.tildify("/home/userX"), "/home/userX")

    def test_fmt_tokens(self):
        self.assertEqual(base.fmt_tokens(None), "0")
        self.assertEqual(base.fmt_tokens(999), "999")
        self.assertEqual(base.fmt_tokens(34500), "34k")
        self.assertEqual(base.fmt_tokens(1_234_567), "1.2M")

    def test_is_timestamp(self):
        self.assertTrue(base.is_timestamp(0))
        self.assertTrue(base.is_timestamp(1.5))
        for bad in (True, "1", None, float("nan"), float("inf"), 1e300, 10**400):
            self.assertFalse(base.is_timestamp(bad), bad)

    def test_iso_to_epoch(self):
        self.assertEqual(base.iso_to_epoch("2026-01-01T12:00:00Z"), 1767268800)
        self.assertIsNone(base.iso_to_epoch(""))
        self.assertIsNone(base.iso_to_epoch("nope"))

    def test_message_text(self):
        self.assertEqual(base.message_text({"content": "hi"}), "hi")
        self.assertEqual(base.message_text({"content": [{"type": "image"}, {"type": "text", "text": "x"}]}), "x")
        self.assertEqual(base.message_text({"content": [{"type": "text"}]}), "")
        self.assertEqual(base.message_text("nope"), "")

    def test_remap(self):
        self.assertEqual(base.remap("/old", "/old", "/new"), "/new")
        self.assertEqual(base.remap("/old/sub", "/old/", "/new/"), "/new/sub")
        self.assertEqual(base.remap("/older", "/old", "/new"), "/older")
        obj = {"cwd": "/old", "workspace_roots": ["/old/a", 3], "x": [{"cwd": "/old"}], "y": {"cwd": 1}}
        self.assertTrue(base.remap_tree(obj, "/old", "/new"))
        self.assertEqual(obj, {"cwd": "/new", "workspace_roots": ["/new/a", 3], "x": [{"cwd": "/new"}], "y": {"cwd": 1}})


class PidAlive(unittest.TestCase):
    def check(self, effect, expected):
        with mock.patch.object(os, "kill", side_effect=effect) as kill:
            self.assertIs(base.pid_alive(42), expected)
        kill.assert_called_once_with(42, 0)

    def test_r_base_2(self):
        self.check(None, True)
        self.check(ProcessLookupError, False)
        self.check(PermissionError, True)

    def test_r_base_3_huge_pid(self):
        self.assertFalse(base.pid_alive(10**100))


class Caches(HomeCase):
    def test_r_cache_1_inode_replacement(self):
        p = write(self.path("f.jsonl"), '{"n":1}\n')
        calls, *fns = numbers()
        self.assertEqual(base.cached_lines(p, *fns)[0], [1])
        self.assertEqual(base.cached(p, lambda q: read(q, "r"))[0], '{"n":1}\n')
        st = os.stat(p)
        write(p + ".new", '{"n":2}\n', mtime=None)
        os.utime(p + ".new", ns=(st.st_atime_ns, st.st_mtime_ns))
        os.replace(p + ".new", p)
        self.assertEqual(base.cached_lines(p, *fns)[0], [2])
        self.assertEqual(base.cached(p, lambda q: read(q, "r"))[0], '{"n":2}\n')

    def test_r_cache_2_unchanged(self):
        p = write(self.path("f.jsonl"), '{"n":1}\n')
        calls, *fns = numbers()
        base.cached_lines(p, *fns)
        base.cached_lines(p, *fns)
        self.assertEqual(len(calls), 1)
        parser = mock.Mock(return_value="x")
        base.cached(p, parser)
        base.cached(p, parser)
        parser.assert_called_once()

    def test_r_cache_3_partial_tail(self):
        p = write(self.path("f.jsonl"), '{"n":1}\n', mtime=None)
        calls, *fns = numbers()
        base.cached_lines(p, *fns)
        with open(p, "a") as f:
            f.write('{"n":2}\n{"n":')
        self.assertEqual(base.cached_lines(p, *fns)[0], [1, 2])
        self.assertEqual(len(calls), 2)
        with open(p, "a") as f:
            f.write('3}\n')
        self.assertEqual(base.cached_lines(p, *fns)[0], [1, 2, 3])
        self.assertEqual(len(calls), 3)

    def test_r_cache_4_complete_record_without_newline(self):
        p = write(self.path("f.jsonl"), '{"n":1}', mtime=None)
        calls, *fns = numbers()
        self.assertEqual(base.cached_lines(p, *fns)[0], [1])
        with open(p, "a") as f:
            f.write('\n{"n":2}\n')
        self.assertEqual(base.cached_lines(p, *fns)[0], [1, 2])

    def test_r_cache_5_bad_line(self):
        p = write(self.path("f.jsonl"), '{"n":1}\nbroken\n{"n":3}\n')
        _calls, *fns = numbers()
        with mock.patch("builtins.print"):
            self.assertEqual(base.cached_lines(p, *fns)[0], [1, 3])

    def test_truncation_restarts(self):
        p = write(self.path("f.jsonl"), '{"n":1}\n{"n":2}\n', mtime=None)
        _calls, *fns = numbers()
        base.cached_lines(p, *fns)
        with open(p, "w") as f:
            f.write('{"n":9}\n{"n":8}\n')  # same size, rewritten in place
        self.assertEqual(base.cached_lines(p, *fns)[0], [9, 8])

    def test_r_cache_6_threads(self):
        p = write(self.path("f.jsonl"), "".join('{"n":%d}\n' % i for i in range(2000)))
        _calls, *fns = numbers()
        results = []
        threads = [threading.Thread(target=lambda: results.append(base.cached_lines(p, *fns)[0])) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertTrue(all(r == list(range(2000)) for r in results))

    def test_prune(self):
        p = write(self.path("f.jsonl"), '{"n":1}\n')
        base.cached_lines(p, *numbers()[1:])
        os.unlink(p)
        base.prune_caches()
        self.assertNotIn(p, base._lines_cache)


class AtomicWrite(HomeCase):
    def test_r_fs_1_failed_rename(self):
        p = write(self.path("d", "f"), "old")
        with mock.patch.object(os, "replace", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                fsutil.atomic_write(p, "new")
        self.assertEqual(read(p, "r"), "old")
        self.assertEqual(os.listdir(self.path("d")), ["f"])

    def test_mode(self):
        p = self.path("f")
        fsutil.atomic_write(p, b"x", 0o640)
        self.assertEqual(os.stat(p).st_mode & 0o777, 0o640)

    def test_r_fs_2_threads(self):
        names = set()
        real = os.open

        def spy(path, *a, **k):
            names.add(path)
            return real(path, *a, **k)
        with mock.patch.object(os, "open", spy):
            ts = [threading.Thread(target=fsutil.atomic_write, args=(self.path("f"), str(i))) for i in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
        self.assertGreaterEqual(len(names), 2)
        self.assertEqual(os.listdir(self.home), ["project", "f"] if os.listdir(self.home)[0] == "project" else ["f", "project"])


class Rewrite(HomeCase):
    SRC = b'{"cwd":"/old"}\nuntouched line\n'
    OUT = b'{"cwd":"/new"}\nuntouched line\n'

    def setUp(self):
        super().setUp()
        self.dir = self.path("w")
        os.makedirs(self.dir)
        old = os.umask(0)
        self.addCleanup(os.umask, old)

    def src(self, mode=0o600):
        p = write(os.path.join(self.dir, "a.jsonl"), self.SRC, mtime=1000)
        os.chmod(p, mode)
        return p

    def test_r_rewrite_1(self):
        for mode in (0o600, 0o640):
            for inplace in (True, False):
                src = self.src(mode)
                dst = src if inplace else os.path.join(self.dir, "b.jsonl")
                mt = os.stat(src).st_mtime_ns
                base.rewrite_jsonl(src, dst, "/old", "/new")
                self.assertEqual(read(dst), self.OUT)
                self.assertEqual(os.stat(dst).st_mode & 0o777, mode)
                self.assertEqual(os.stat(dst).st_mtime_ns, mt)
                if not inplace:
                    self.assertEqual(read(src), self.SRC)
                    os.unlink(dst)

    def test_r_rewrite_2_temp_is_private(self):
        src = self.src(0o640)
        seen = []
        real = base.remap_tree

        def spy(*a):
            extra = [n for n in os.listdir(self.dir) if n != "a.jsonl"]
            seen.append((extra, [os.stat(os.path.join(self.dir, n)).st_mode & 0o777 for n in extra]))
            return real(*a)
        with mock.patch.object(base, "remap_tree", spy):
            base.rewrite_jsonl(src, src, "/old", "/new")
        self.assertEqual(len(seen[0][0]), 1)
        self.assertTrue(seen[0][0][0].endswith(".atree-tmp"))
        self.assertEqual(seen[0][1], [0o600])
        self.assertEqual(os.listdir(self.dir), ["a.jsonl"])

    def test_r_rewrite_3_failures_leave_everything(self):
        for target in ("remap_tree", "fsync", "utime", "replace"):
            src = self.src()
            dst = write(os.path.join(self.dir, "b.jsonl"), b"previous\n", mtime=5)
            before = {n: (read(os.path.join(self.dir, n)), os.stat(os.path.join(self.dir, n)).st_mtime_ns)
                      for n in os.listdir(self.dir)}
            where = base if target == "remap_tree" else os
            with mock.patch.object(where, target, side_effect=RuntimeError(target)):
                with self.assertRaisesRegex(RuntimeError, target):
                    base.rewrite_jsonl(src, dst, "/old", "/new")
            after = {n: (read(os.path.join(self.dir, n)), os.stat(os.path.join(self.dir, n)).st_mtime_ns)
                     for n in os.listdir(self.dir)}
            self.assertEqual(before, after, target)

    def test_r_rewrite_4_fsync_sees_full_temp(self):
        src = self.src()
        real = os.fsync
        calls = []

        def spy(fd):
            tmp = [n for n in os.listdir(self.dir) if n.endswith(".atree-tmp")][0]
            os.fsync is spy and calls.append(read(os.path.join(self.dir, tmp)))
            self.assertEqual(read(src), self.SRC)
            return real(fd)
        with mock.patch.object(os, "fsync", spy):
            base.rewrite_jsonl(src, src, "/old", "/new")
        self.assertEqual(calls, [self.OUT])

    def test_r_rewrite_5_symlink_decoy(self):
        src = self.src()
        victim = write(self.path("victim"), "keep")
        os.symlink(victim, src + ".atree-tmp")
        base.rewrite_jsonl(src, src, "/old", "/new")
        self.assertEqual(os.readlink(src + ".atree-tmp"), victim)
        self.assertEqual(read(victim, "r"), "keep")

    def test_r_rewrite_6_new_inode(self):
        src = self.src()
        st = os.stat(src)
        base.rewrite_jsonl(src, src, "/old", "/new")
        st2 = os.stat(src)
        self.assertNotEqual(st.st_ino, st2.st_ino)
        self.assertEqual((st.st_size, st.st_mtime_ns), (st2.st_size, st2.st_mtime_ns))

    def test_non_ascii_kept(self):
        src = write(os.path.join(self.dir, "a.jsonl"), '{"cwd":"/old","t":"日本"}\n'.encode())
        base.rewrite_jsonl(src, src, "/old", "/new")
        self.assertEqual(read(src, "r"), '{"cwd":"/new","t":"日本"}\n')


if __name__ == "__main__":
    unittest.main()
