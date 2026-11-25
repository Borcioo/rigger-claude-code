"""Shared fixtures of the review_scope tests: a repository per test in a git environment free of the
host's config, and the base of the review.paths tests. Not a test file: the parts import it."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOL_DIR, "review_scope.py")
sys.path.insert(0, TOOL_DIR)
sys.path.insert(1, os.path.join(os.path.dirname(TOOL_DIR), "assemble"))

import config  # noqa: E402
import review_scope  # noqa: E402
import snapshot  # noqa: E402

REAL_RUN = subprocess.run
REAL_POPEN = subprocess.Popen
UNICODE_NAME = "zażółć ą.py"
EXPECTED = [
    ("a.py", "review"),
    ("b.py", "skip: deleted"),
    ("c.bin", "review: binary added"),
    ("d.py", "skip: deleted"),
    ("e.py", "review"),
    ("tests/test_x.py", "review"),
    (UNICODE_NAME, "review"),
]


class RecordingPipe:
    """A Popen stdin that keeps a copy of every byte written to it."""

    def __init__(self, pipe, sink):
        self.pipe = pipe
        self.sink = sink

    def write(self, data):
        self.sink.append(bytes(data))
        return self.pipe.write(data)

    def __getattr__(self, name):
        return getattr(self.pipe, name)


def remove_tree(path):
    def onerror(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    shutil.rmtree(path, onerror=onerror)


class GitCase(unittest.TestCase):
    """Each test gets its own repository and a git environment free of the host's config."""

    def setUp(self):
        self.tmp = Path(os.path.abspath(tempfile.mkdtemp(prefix="review-scope-test-")))
        self.addCleanup(remove_tree, self.tmp)
        empty_config = self.tmp / "gitconfig"
        empty_config.write_bytes(b"")
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith("GIT_") and key.upper() != "CLAUDE_CODE_SESSION_ID"}
        self.config_dir = self.tmp / "config"
        env.update(
            CLAUDE_CONFIG_DIR=str(self.config_dir),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(empty_config),
            GIT_CEILING_DIRECTORIES=str(self.tmp),
            GIT_AUTHOR_NAME="test",
            GIT_AUTHOR_EMAIL="test",
            GIT_COMMITTER_NAME="test",
            GIT_COMMITTER_EMAIL="test",
        )
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")

    def git(self, *args, repo=None):
        proc = REAL_RUN(
            ["git"] + list(args),
            cwd=str(repo or self.repo),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        return proc.stdout.decode("utf-8").strip()

    def write(self, name, data):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def two_commits(self):
        self.write("a.py", b"a = 1\n")
        self.write("b.py", b"b = 1\n")
        self.write("d.py", b"def d():\n    return 'a file long enough to be seen as renamed'\n")
        base = self.commit("base")
        self.write("a.py", b"a = 2\n")
        (self.repo / "b.py").unlink()
        self.write("c.bin", b"\x00\x01binary\x00")
        os.rename(str(self.repo / "d.py"), str(self.repo / "e.py"))
        self.write(UNICODE_NAME, "x = 'ą'\n".encode("utf-8"))
        self.write("tests/test_x.py", b"def test_x():\n    pass\n")
        head = self.commit("head")
        return base, head

    def cli(self, *args):
        out = io.StringIO()
        buffer = io.BytesIO()
        fake_stdout = mock.Mock()
        fake_stdout.buffer = buffer
        with mock.patch.object(sys, "stdout", fake_stdout), contextlib.redirect_stderr(out):
            code = review_scope.main(list(args))
        return code, buffer.getvalue().decode("utf-8")

    @contextlib.contextmanager
    def git_calls(self, batch_input=None):
        """Every git start, through run or Popen, as (argv, env); `batch_input` collects what reaches a Popen's stdin."""
        calls = []

        def spy(argv, *args, **kwargs):
            calls.append((list(argv), kwargs.get("env")))
            return REAL_RUN(argv, *args, **kwargs)

        def spy_popen(argv, *args, **kwargs):
            # subprocess.run starts a Popen too, with no stdin pipe: that start is already recorded by `spy`.
            if kwargs.get("stdin") is not subprocess.PIPE:
                return REAL_POPEN(argv, *args, **kwargs)
            calls.append((list(argv), kwargs.get("env")))
            proc = REAL_POPEN(argv, *args, **kwargs)
            if batch_input is not None:
                proc.stdin = RecordingPipe(proc.stdin, batch_input)
            return proc

        with mock.patch.object(review_scope.subprocess, "run", spy), \
                mock.patch.object(review_scope.subprocess, "Popen", spy_popen):
            yield calls

    @contextlib.contextmanager
    def no_git(self):
        def refuse(argv, *args, **kwargs):
            raise AssertionError("git was started: %r" % (argv,))

        with mock.patch.object(review_scope.subprocess, "run", refuse), \
                mock.patch.object(review_scope.subprocess, "Popen", refuse):
            yield

    def files(self, files, message="commit"):
        """Write (bytes) or delete (None) each file, then commit; the new commit id."""
        for name, data in files.items():
            if data is None:
                (self.repo / name).unlink()
            else:
                self.write(name, data)
        return self.commit(message)

    def verdicts(self, base, head, rules=()):
        return dict(review_scope.scope(self.repo, base, head, rules))


SESSION = "review-scope-session"


class PathRuleCase(GitCase):
    def project(self, paths, **extra):
        body = {"schema": 1, "review": {"paths": paths}}
        body.update(extra)
        return json.dumps(body).encode("utf-8")

    def write_snapshot(self, l2=(), l3=None, l4=None, modules=(), effective=None):
        paths = {1: "core/rigger-defaults.json", 3: str(self.config_dir / "rigger" / "config.json"),
                 4: str(self.repo / ".claude" / "rigger.json")}
        got = config.resolve({"schema": 1}, list(l2), l3 or {}, l4 or {}, paths)
        data = {key: [] for key in snapshot.SNAPSHOT_KEYS}
        data.update({
            "format": snapshot.SNAPSHOT_FORMAT,
            "config": {"effective": effective if effective is not None else got.effective,
                       "sources": [[list(path), source] for path, source in sorted(got.sources.items())]},
            "modules": [{"name": name, "root": Path(root).as_posix() if os.name == "nt" else str(root)}
                        for name, root in modules],
        })
        self.assertIsNotNone(snapshot.write_snapshot(self.config_dir, SESSION, data))
        return snapshot.read_snapshot(self.config_dir, SESSION)

    def run_cli(self, *args):
        code, text = self.cli(*args)
        self.assertEqual(code, 0, text)
        return text.split("\n")

    def same_file(self, a, b):
        self.assertEqual(os.path.normcase(os.path.abspath(a)), os.path.normcase(os.path.abspath(str(b))))

