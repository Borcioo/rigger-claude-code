"""review_scope: the list of files a review covers, and the refusals that guard the git calls.

python -I -X utf8 test_review_scope.py
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
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


class ScopeTests(GitCase):
    def test_lists_every_changed_path_with_its_verdict(self):
        self.two_commits()
        with self.git_calls() as calls:
            result = review_scope.scope(self.repo, "HEAD~1", "HEAD")
        self.assertEqual(result, EXPECTED)
        base_id = self.git("rev-parse", "HEAD~1")
        head_id = self.git("rev-parse", "HEAD")
        diffs = [argv for argv, _env in calls if "diff" in argv]
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0][-2:], [base_id, head_id])
        self.assertNotIn("HEAD", diffs[0])
        self.assertNotIn("HEAD~1", diffs[0])

    def test_a_plain_name_status_would_join_the_rename(self):
        base, head = self.two_commits()
        plain = self.git("diff", "--name-status", base, head)
        self.assertTrue(any(line.startswith("R") for line in plain.splitlines()), plain)
        paths = [path for path, _verdict in review_scope.scope(self.repo, base, head)]
        self.assertIn("d.py", paths)
        self.assertIn("e.py", paths)

    def test_a_test_file_is_never_skipped(self):
        base, head = self.two_commits()
        result = dict(review_scope.scope(self.repo, base, head))
        self.assertEqual(result["tests/test_x.py"], "review")

    def test_same_commit_gives_zero_files(self):
        self.write("a.py", b"a = 1\n")
        head = self.commit("one")
        self.assertEqual(review_scope.scope(self.repo, head, head), [])

    def test_not_a_repository_refuses(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        for folder in (plain, self.tmp / "missing"):
            with self.subTest(folder=folder.name):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    review_scope.scope(folder, "HEAD~1", "HEAD")
                self.assertIn("not a git repository", str(caught.exception))
                code, text = self.cli("--base", "HEAD~1", "--repo", str(folder))
                self.assertEqual(code, 2)
                self.assertTrue(text.startswith("review-scope: refused: not a git repository"), text)

    def test_a_git_failure_or_an_unexpected_diff_refuses(self):
        base, head = self.two_commits()

        def fake_diff(result):
            def run(argv, *args, **kwargs):
                if "diff" not in argv:
                    return REAL_RUN(argv, *args, **kwargs)
                if isinstance(result, BaseException):
                    raise result
                code, stdout = result
                return subprocess.CompletedProcess(argv, code, stdout, b"")

            return mock.patch.object(review_scope.subprocess, "run", run)

        rows = [
            ((128, b""), "git diff failed with exit 128"),
            (subprocess.TimeoutExpired(["git"], 60), "git diff took over 60 s"),
            (OSError("no git"), "git could not start"),
            ((0, b"M\0a.py\0M\0"), "git diff printed an odd number of fields"),
            ((0, b"R100\0d.py\0e.py\0M\0"), "git diff printed an unexpected status: R100"),
            ((0, b"C\0a.py\0"), "git diff printed an unexpected status: C"),
            ((0, b"M\0\xff.py\0"), "path is not UTF-8"),
        ]
        for result, reason in rows:
            with self.subTest(reason=reason), fake_diff(result):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    review_scope.scope(self.repo, base, head)
                self.assertIn(reason, str(caught.exception))

    def test_annotated_tag_of_a_commit_resolves_to_the_commit(self):
        self.write("a.py", b"a = 1\n")
        head = self.commit("one")
        self.git("tag", "-a", "-m", "release", "v1", head)
        tag_object = self.git("rev-parse", "v1")
        self.assertNotEqual(tag_object, head)
        self.assertEqual(review_scope.resolve_commit(self.repo, "v1"), head)


class RevisionRefusalTests(GitCase):
    def test_revision_that_looks_like_an_option_is_refused(self):
        self.write("a.py", b"a = 1\n")
        head = self.commit("one")
        blob = self.git("rev-parse", "HEAD:a.py")
        self.git("tag", "-a", "-m", "blob", "blobtag", blob)
        out_file = self.tmp / "x"
        python_rows = [
            ("--output=%s" % out_file.as_posix(), "starts with '-'"),
            ("-p", "starts with '-'"),
            ("", "empty"),
            ("a b", "whitespace"),
            ("a\x01b", "control character"),
            ("a\x7fb", "control character"),
        ]
        for rev, reason in python_rows:
            with self.subTest(rev=rev):
                with self.no_git():
                    with self.assertRaises(review_scope.ScopeError) as caught:
                        review_scope.scope(self.repo, rev, head)
                    self.assertIn(reason, str(caught.exception))
                    with self.assertRaises(review_scope.ScopeError):
                        review_scope.scope(self.repo, head, rev)
                    code, text = self.cli("--base", rev, "--head", head, "--repo", str(self.repo))
                self.assertEqual(code, 2)
                self.assertTrue(text.startswith("review-scope: refused: "), text)
                self.assertIn(reason, text)
                self.assertFalse(out_file.exists())
        # A tab is both whitespace and a control character: it is refused either way.
        with self.subTest(rev="tab"), self.no_git():
            with self.assertRaises(review_scope.ScopeError):
                review_scope.scope(self.repo, "a\tb", head)
        for rev in ("no-such-rev", "blobtag"):
            with self.subTest(rev=rev):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    review_scope.scope(self.repo, rev, head)
                self.assertIn("does not name a commit: %s" % rev, str(caught.exception))
                code, text = self.cli("--base", rev, "--head", head, "--repo", str(self.repo))
                self.assertEqual(code, 2)
                self.assertIn("review-scope: refused: revision does not name a commit: %s" % rev, text)
        self.assertFalse(out_file.exists())

    def test_cli_takes_the_next_token_verbatim(self):
        self.write("a.py", b"a = 1\n")
        head = self.commit("one")
        with self.no_git():
            code, text = self.cli("--base", "-p", "--head", head, "--repo", str(self.repo))
        self.assertEqual((code, text.splitlines()[-1:]), (2, ["review-scope: refused: revision starts with '-': -p"]))


class DriverTests(GitCase):
    def marker_script(self, name, marker, body):
        script = self.tmp / name
        script.write_bytes(("#!/bin/sh\necho ran > '%s'\n%s" % (marker.as_posix(), body)).encode("utf-8"))
        os.chmod(str(script), 0o755)
        return script

    def test_drivers_of_the_repository_do_not_run(self):
        ext_marker = self.tmp / "ext-marker"
        conv_marker = self.tmp / "conv-marker"
        ext = self.marker_script("ext.sh", ext_marker, "")
        conv = self.marker_script("conv.sh", conv_marker, 'cat "$1"\n')
        self.git("config", "diff.external", ext.as_posix())
        self.git("config", "diff.marker.textconv", conv.as_posix())
        attributes = self.repo / ".git" / "info" / "attributes"
        attributes.parent.mkdir(parents=True, exist_ok=True)
        attributes.write_bytes(b"* diff=marker\n")
        base, head = self.two_commits()
        # Controls: each driver runs on a plain diff of these commits.
        self.git("diff", base, head)
        self.assertTrue(ext_marker.exists())
        self.git("diff", "--no-ext-diff", base, head)
        self.assertTrue(conv_marker.exists())
        ext_marker.unlink()
        conv_marker.unlink()
        self.assertEqual(review_scope.scope(self.repo, base, head), EXPECTED)
        self.assertFalse(ext_marker.exists())
        self.assertFalse(conv_marker.exists())

    def test_drivers_of_the_environment_do_not_run(self):
        marker = self.tmp / "env-marker"
        ext = self.marker_script("ext.sh", marker, "")
        base, head = self.two_commits()
        with mock.patch.dict(os.environ, {"GIT_EXTERNAL_DIFF": ext.as_posix(), "GIT_DIFF_OPTS": "--unified=0"}):
            self.git("diff", base, head)
            self.assertTrue(marker.exists())
            marker.unlink()
            with self.git_calls() as calls:
                self.assertEqual(review_scope.scope(self.repo, base, head), EXPECTED)
        self.assertFalse(marker.exists())
        self.assertTrue(calls)
        for argv, env in calls:
            with self.subTest(argv=argv):
                self.assertIsNotNone(env)
                self.assertNotIn("GIT_EXTERNAL_DIFF", env)
                self.assertNotIn("GIT_DIFF_OPTS", env)


EXE = ".exe" if os.name == "nt" else ""


def ctypes_volume_name(mount):
    """The `\\\\?\\Volume{...}\\` name of the volume mounted at `mount` (Windows)."""
    import ctypes
    buffer = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(mount, buffer, len(buffer)):
        raise OSError("no volume name for %s" % mount)
    return buffer.value


def admin_share(case, path):
    """`path` spelled through the administrative share of its drive on this machine; the test is skipped when
    the share cannot be opened, and fails instead when CI is set, so a CI runner cannot skip it unseen."""
    drive, rest = os.path.splitdrive(os.path.abspath(str(path)))
    share = "\\\\localhost\\%s$%s" % (drive[:1], rest)
    probe = "\\\\localhost\\%s$\\" % drive[:1]
    if not os.path.isdir(probe):
        reason = "the admin share %s cannot be opened" % probe
        if os.environ.get("CI"):
            case.fail(reason)
        case.skipTest(reason)
    return share


@contextlib.contextmanager
def working_directory(path):
    before = os.getcwd()
    os.chdir(str(path))
    try:
        yield
    finally:
        os.chdir(before)


class ProgramLookupTests(GitCase):
    def setUp(self):
        super().setUp()
        self.real_path = os.environ.get("PATH", "")
        self.good = self.tmp / "good"
        self.tool = self.program(self.good, "tool")
        self.elsewhere = self.tmp / "elsewhere"
        self.elsewhere.mkdir()

    def program(self, folder, name, ext=EXE, executable=True):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name + ext)
        path.write_bytes(b"#!/bin/sh\nexit 0\n")
        os.chmod(str(path), 0o755 if executable else 0o644)
        return path

    def find(self, entries, name="tool", cwd=None, exts=(EXE,)):
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(entries)}):
            with working_directory(cwd or self.elsewhere):
                return review_scope.find_program(name, self.repo, exts)

    def same(self, a, b):
        self.assertEqual(os.path.normcase(os.path.abspath(str(a))), os.path.normcase(os.path.abspath(str(b))))

    def test_lookup_skips_every_unsafe_entry(self):
        sibling = Path(str(self.repo) + "-tools")
        self.program(self.repo, "tool")
        self.program(self.repo / "bin", "tool")
        self.program(self.elsewhere, "tool")
        self.program(self.elsewhere / "bin", "tool")
        self.program(self.tmp / "rel", "tool")
        (self.tmp / "dirs" / ("tool" + EXE)).mkdir(parents=True)
        self.program(self.tmp / "noexec", "tool", executable=False)
        nested_cwd = self.elsewhere / "cwd"
        nested_cwd.mkdir()
        # (row, the unsafe entry put before the good one, the working directory)
        rows = [
            ("the repository itself", str(self.repo), self.elsewhere),
            ("a folder inside the repository", str(self.repo / "bin"), self.elsewhere),
            ("the working directory", str(self.elsewhere), self.elsewhere),
            ("a folder inside the working directory", str(self.elsewhere / "bin"), self.elsewhere),
            # Resolves to <tmp>/rel, outside both: only the absolute-entry rule skips it.
            ("a relative entry", os.path.join("..", "..", "rel"), nested_cwd),
            ("an empty entry", "", self.elsewhere),
            ("a folder holding a directory named like the program", str(self.tmp / "dirs"), self.elsewhere),
        ]
        if os.name == "nt":
            self.program(self.tmp / "rootrel", "tool")
            rows += [
                ("the repository spelled in another case", str(self.repo).swapcase(), self.elsewhere),
                ("the repository with the extended prefix", "\\\\?\\" + str(self.repo), self.elsewhere),
                ("a folder inside the repository with the extended prefix in forward slashes",
                 "//?/" + str(self.repo / "bin").replace("\\", "/"), self.elsewhere),
                ("the repository with the device prefix", "\\\\.\\" + str(self.repo), self.elsewhere),
                # Outside both, but read against another drive's root by a child on another drive.
                ("a root-relative entry", os.path.splitdrive(str(self.tmp / "rootrel"))[1], self.elsewhere),
                ("a drive-relative entry", os.path.splitdrive(str(self.tmp))[0] + os.path.join("..", "..", "rel"),
                 nested_cwd),
            ]
        else:
            rows.append(("a file that is not executable", str(self.tmp / "noexec"), self.elsewhere))
        for label, unsafe, cwd in rows:
            with self.subTest(row=label):
                self.same(self.find([unsafe, str(self.good)], cwd=cwd), self.tool)
        # Not over-excluded: a folder whose name only starts with the repository's is outside it.
        sibling_tool = self.program(sibling, "tool")
        self.same(self.find([str(sibling), str(self.good)]), sibling_tool)

    def link(self, link, target):
        """A junction on Windows, a symlink on POSIX: `link` leads to the folder `target`."""
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(str(target), str(link))
        return link

    def test_lookup_resolves_links_on_each_side(self):
        """An entry, the repository or the working directory spelled through a link is compared by its real path."""
        self.program(self.repo / "bin", "tool")
        self.program(self.elsewhere / "bin", "tool")
        repo_link = self.link(self.tmp / "repo-link", self.repo)
        cwd_link = self.link(self.tmp / "cwd-link", self.elsewhere)
        into_repo = self.link(self.tmp / "into-repo", self.repo / "bin")
        into_cwd = self.link(self.tmp / "into-cwd", self.elsewhere / "bin")
        # (row, the unsafe entry put before the good one, the repository as given, the working directory)
        rows = [
            ("an entry linked into the repository", str(into_repo), self.repo, self.elsewhere),
            ("the repository given through a link", str(self.repo / "bin"), repo_link, self.elsewhere),
            ("an entry linked into the working directory", str(into_cwd), self.repo, self.elsewhere),
            ("the working directory entered through a link", str(self.elsewhere / "bin"), self.repo, cwd_link),
        ]
        for label, unsafe, repo, cwd in rows:
            with self.subTest(row=label):
                with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([unsafe, str(self.good)])}):
                    with working_directory(cwd):
                        self.same(review_scope.find_program("tool", repo, (EXE,)), self.tool)

    def test_an_entry_spelled_inside_through_a_link_that_leads_outside_is_not_searched(self):
        """The repository or the working directory can point its link elsewhere at any time: an entry spelled
        inside either is refused by its spelling, though its real path and every folder above it are outside."""
        self.program(self.repo / "bin", "tool")
        rows = [
            ("a link in the repository", self.link(self.repo / "out-link", self.good)),
            ("a link in the working directory", self.link(self.elsewhere / "out-link", self.good)),
        ]
        for label, entry in rows:
            with self.subTest(row=label):
                self.same(os.path.realpath(str(entry)), os.path.realpath(str(self.good)))
                self.assertIs(review_scope.searchable(str(entry), self.repo, str(self.elsewhere)), False)
        # Not over-excluded: the folder the links lead to, spelled as itself, is still searched.
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(self.elsewhere)), True)

    @unittest.skipUnless(os.name == "nt", "the extended and device prefixes are Windows spellings")
    def test_a_prefixed_entry_is_read_without_its_prefix(self):
        """A prefixed entry outside both is still searched; one the prefix cannot be removed from is not."""
        good = str(self.good)
        volume = ctypes_volume_name(os.path.splitdrive(good)[0] + "\\")
        rows = [  # (row, entry, searchable)
            ("the extended prefix", "\\\\?\\" + good, True),
            ("the extended prefix in forward slashes", "//?/" + good.replace("\\", "/"), True),
            ("the device prefix", "\\\\.\\" + good, True),
            ("a doubled prefix", "\\\\?\\\\\\?\\" + good, False),
            ("the extended UNC prefix", "\\\\?\\UNC\\localhost\\share\\bin", True),
            ("the device UNC prefix", "\\\\.\\UNC\\localhost\\share\\bin", True),
            ("a volume name", volume + os.path.splitdrive(good)[1].lstrip("\\"), False),
            ("a device with no drive", "\\\\?\\GLOBALROOT\\Device\\HarddiskVolume1\\bin", False),
        ]
        for label, entry, expected in rows:
            with self.subTest(row=label, entry=entry):
                self.assertIs(review_scope.searchable(entry, self.repo, str(self.elsewhere)), expected)

    def case_twin(self, folder):
        """`folder` with its last name in the other case, when the file system reads it as the same folder."""
        twin = folder.parent / folder.name.swapcase()
        if not twin.is_dir() or not os.path.samefile(str(twin), str(folder)):
            reason = "case-sensitive file system at %s (on WSL set TMPDIR to a /mnt/<drive> folder)" % folder
            if sys.platform == "darwin":  # APFS is case-insensitive by default: a skip here would hide the proof
                self.fail(reason)
            self.skipTest(reason)
        return twin

    @unittest.skipUnless(os.name == "nt", "the admin share is a Windows spelling")
    def test_admin_share_entry_is_not_searched(self):
        """The repository or the working directory spelled through the `\\\\localhost\\<drive>$` share is the same
        folder by file identity, though no spelling or real path matches."""
        (self.repo / "bin").mkdir()
        (self.elsewhere / "bin").mkdir()
        rows = [
            ("the repository", admin_share(self, self.repo)),
            ("a folder inside the repository", admin_share(self, self.repo / "bin")),
            ("a missing folder inside the repository", os.path.join(admin_share(self, self.repo), "nope", "deeper")),
            ("the working directory", admin_share(self, self.elsewhere)),
            ("a folder inside the working directory", admin_share(self, self.elsewhere / "bin")),
        ]
        for label, unsafe in rows:
            with self.subTest(row=label):
                self.assertIs(review_scope.searchable(unsafe, self.repo, str(self.elsewhere)), False)
        # Not over-excluded: a folder outside both, spelled through the share, is still searched.
        self.assertIs(review_scope.searchable(admin_share(self, self.good), self.repo, str(self.elsewhere)), True)

    def test_lookup_skips_the_repository_and_the_cwd_spelled_in_another_case(self):
        """On a case-insensitive file system (macOS by default, WSL's /mnt/<drive>, Windows) another spelling of
        the repository or the working directory is the same folder."""
        (self.repo / "bin").mkdir()
        (self.elsewhere / "bin").mkdir()
        repo_twin = self.case_twin(self.repo)
        cwd_twin = self.case_twin(self.elsewhere)
        rows = [
            ("the repository", str(repo_twin)),
            ("a folder inside the repository", str(repo_twin / "bin")),
            ("a missing folder inside the repository", str(repo_twin / "nope" / "deeper")),
            ("the working directory", str(cwd_twin)),
            ("a folder inside the working directory", str(cwd_twin / "bin")),
        ]
        for label, unsafe in rows:
            with self.subTest(row=label):
                self.assertIs(review_scope.searchable(unsafe, self.repo, str(self.elsewhere)), False)
        # Not over-excluded: an existing folder beside them, outside both, is still searched.
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(self.elsewhere)), True)

    def test_no_entry_is_searched_when_the_working_directory_cannot_be_read(self):
        """Without the folder's identity another spelling of it cannot be ruled out."""
        missing = self.tmp / "gone"
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(self.elsewhere)), True)
        self.assertIs(review_scope.searchable(str(self.good), self.repo, str(missing)), False)

    @unittest.skipUnless(os.name == "nt", "batch files exist on Windows only")
    def test_git_lookup_never_takes_a_batch_file(self):
        batch = self.tmp / "batch"
        self.program(batch, "git", ".cmd")
        self.program(batch, "git", ".bat")
        real = self.program(self.good, "git")
        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join([str(batch), str(self.good)])}):
            with working_directory(self.elsewhere):
                self.same(review_scope.find_git(self.repo), real)

    def test_lookup_refusals(self):
        with self.assertRaises(review_scope.ScopeError) as caught:
            self.find([str(self.repo), str(self.elsewhere)])
        self.assertIn("tool not found in an absolute PATH entry outside the repository and the working directory",
                      str(caught.exception))
        for name in ("", "a/tool", "a\\tool"):
            with self.subTest(name=name):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    self.find([str(self.good)], name=name)
                self.assertIn("not a bare program name", str(caught.exception))
        for exts in ((".exe", ".cmd"), (".BAT",)):
            with self.subTest(exts=exts):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    self.find([str(self.good)], exts=exts)
                self.assertIn("a batch file is never run", str(caught.exception))

    def test_a_planted_git_is_never_run(self):
        base, head = self.two_commits()
        real_git = shutil.which("git", path=self.real_path)
        self.assertTrue(real_git and os.path.isabs(real_git))
        marker = self.tmp / "plant-marker"
        if os.name == "nt":
            plant = self.repo / "git.exe"
            shutil.copy2(sys.executable, str(plant))
            for dll in Path(os.path.dirname(sys.executable)).glob("*.dll"):
                shutil.copy2(str(dll), str(self.repo / dll.name))
        else:
            plant = self.repo / "git"
            plant.write_bytes(("#!/bin/sh\necho ran > '%s'\necho planted\nexit 1\n" % marker.as_posix()).encode("utf-8"))
            os.chmod(str(plant), 0o755)
        planted_path = os.pathsep.join([str(self.repo), self.real_path])
        # Control: an ordinary lookup over this PATH finds the plant, and it runs.
        naive = shutil.which("git", path=planted_path)
        self.same(naive, plant)
        ran = REAL_RUN([naive, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        self.assertIn(b"Python" if os.name == "nt" else b"planted", ran.stdout)
        if marker.exists():
            marker.unlink()
        for cwd, path in ((self.repo, self.real_path), (self.elsewhere, planted_path), (self.repo, "." + os.pathsep + self.real_path)):
            with self.subTest(cwd=cwd.name, path=path.split(os.pathsep)[0]):
                with mock.patch.dict(os.environ, {"PATH": path}), working_directory(cwd), self.git_calls() as calls:
                    self.assertEqual(review_scope.scope(self.repo, base, head), EXPECTED)
                self.assertTrue(calls)
                for argv, _env in calls:
                    self.same(argv[0], real_git)
                self.assertFalse(marker.exists())


class EnvironmentTests(GitCase):
    def test_inherited_git_variables_do_not_reach_git(self):
        base, head = self.two_commits()
        other = self.tmp / "other"
        other.mkdir()
        self.git("init", "-q", repo=other)
        (other / "o.py").write_bytes(b"o = 1\n")
        self.git("add", "-A", repo=other)
        self.git("commit", "-q", "-m", "other", repo=other)
        other_head = self.git("rev-parse", "HEAD", repo=other)
        inherited = {
            "GIT_DIR": str(other / ".git"),
            "GIT_WORK_TREE": str(other),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.quotepath",
            "GIT_CONFIG_VALUE_0": "on",
        }
        with mock.patch.dict(os.environ, inherited):
            # Control: these variables send an ordinary git call in this repository to the other one.
            self.assertEqual(self.git("rev-parse", "HEAD"), other_head)
            with self.git_calls() as calls:
                self.assertEqual(review_scope.scope(self.repo, base, head), EXPECTED)
                self.assertEqual(review_scope.resolve_commit(self.repo, "HEAD"), head)
        self.assertTrue(calls)
        for argv, env in calls:
            with self.subTest(argv=argv[1:]):
                self.assertIsNotNone(env)
                self.assertEqual([key for key in env if key.upper().startswith("GIT_")], [])


class PathRefusalTests(GitCase):
    @unittest.skipIf(os.name == "nt", "Windows cannot name a file with a tab or a line break; runs under WSL and on Linux and macOS")
    def test_a_path_with_a_tab_or_a_line_break_refuses(self):
        self.write("a.py", b"a = 1\n")
        base = self.commit("base")
        for name in ("tab\tname.py", "line\nbreak.py", "carriage\rreturn.py"):
            with self.subTest(name=name):
                self.write(name, b"x = 1\n")
                head = self.commit("add")
                with self.assertRaises(review_scope.ScopeError) as caught:
                    review_scope.scope(self.repo, base, head)
                self.assertIn("path holds a tab or a line break", str(caught.exception))
                code, text = self.cli("--base", base, "--head", head, "--repo", str(self.repo))
                self.assertEqual(code, 2)
                self.assertTrue(text.startswith("review-scope: refused: path holds a tab or a line break"), text)
                (self.repo / name).unlink()
                self.commit("remove")


MARKED = b"# @generated by a tool\nx = 1\n"
NUL_TEXT = b"PNG\x00\x01binary\n"
LOCKFILES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "composer.lock",
             "Cargo.lock", "poetry.lock", "uv.lock", "Pipfile.lock", "Gemfile.lock", "go.sum")


def rule(glob, rules=None, skip=None, level=4, source="L4:test"):
    return review_scope.PathRule(glob, rules, skip, level, source)


class SkipReasonTests(GitCase):
    """D60: each skip reason, the "existed at base" clauses, the test-file rule and the binary prefix."""

    def test_each_reason_on_a_path_that_existed_at_base(self):
        base_files = {
            "vendor/lib.js": b"v = 1\n",
            "node_modules/p/index.js": b"n = 1\n",
            "third_party/x.c": b"int x;\n",
            "third-party/y.c": b"int y;\n",
            "src/vendor/z.go": b"package z\n",
            "gen_a.py": MARKED,
            "gen_b.go": b"// Code from a tool. DO NOT EDIT.\npackage b\n",
            "gen_c.ts": b"/* auto-generated */\nlet c = 1\n",
            "gen_line5.py": b"\n\n\n\n# @generated\n",
            "late_marker.py": b"\n\n\n\n\n# @generated\n",
            "app.min.js": b"var a=1;\n",
            "app.min.css": b"a{}\n",
            "logo.png": NUL_TEXT,
            "late_nul.dat": b"a" * 8000 + b"\x00\n",
            "plain.py": b"p = 1\n",
            "vendored.txt": b"no vendor segment\n",
            "tools/vendor": b"#!/bin/sh\n",
        }
        base = self.files(base_files, "base")
        head_files = {name: data + b"y\n" for name, data in base_files.items()}
        head_files.update({name: b"lock 1\n" for name in LOCKFILES})
        head_files["sub/yarn.lock"] = b"lock 2\n"
        head = self.files(head_files, "head")
        expected = {
            "vendor/lib.js": "skip: vendored",
            "node_modules/p/index.js": "skip: vendored",
            "third_party/x.c": "skip: vendored",
            "third-party/y.c": "skip: vendored",
            "src/vendor/z.go": "skip: vendored",
            "gen_a.py": "skip: generated",
            "gen_b.go": "skip: generated",
            "gen_c.ts": "skip: generated",
            "gen_line5.py": "skip: generated",
            "late_marker.py": "review",
            "app.min.js": "skip: generated",
            "app.min.css": "skip: generated",
            "logo.png": "skip: binary",
            "late_nul.dat": "review",
            "plain.py": "review",
            "vendored.txt": "review",
            "tools/vendor": "review",
            "sub/yarn.lock": "skip: lockfile",
        }
        expected.update({name: "skip: lockfile" for name in LOCKFILES})
        got = self.verdicts(base, head)
        for path, verdict in expected.items():
            with self.subTest(path=path):
                self.assertEqual(got.get(path), verdict)
        self.assertEqual(set(got), set(expected))

    def test_a_skip_the_diff_creates_is_reviewed(self):
        base = self.files({"plain.py": b"p = 1\n", "unmarked.py": MARKED, "mod.js": b"var m = 1;\n",
                           "vendor/old.js": b"o = 1\n"}, "base")
        head = self.files({
            "plain.py": b"# @generated\np = 1\n",
            "unmarked.py": b"x = 1\n",
            "new_marked.py": MARKED,
            "lib/vendor/new.js": b"n = 1\n",
            "vendor/added.js": b"a = 1\n",
            "x.min.js": b"var x=1;\n",
            "blob.bin": NUL_TEXT,
            "mod.js": b"var m = 1;\x00\n",
        }, "head")
        expected = {
            "plain.py": "review",
            "unmarked.py": "review",
            "new_marked.py": "review",
            "lib/vendor/new.js": "review",
            "vendor/added.js": "review",
            "x.min.js": "review",
            "blob.bin": "review: binary added",
            "mod.js": "review: binary added",
        }
        got = self.verdicts(base, head)
        for path, verdict in expected.items():
            with self.subTest(path=path):
                self.assertEqual(got.get(path), verdict)

    def test_test_files_are_never_skipped_without_a_path_rule(self):
        files = {
            "vendor/pkg/test_x.py": b"def test_x():\n    pass\n",
            "tests/fixtures/package-lock.json": b"{}\n",
            "src/__tests__/gen.js": b"// @generated\n",
            "spec/app.min.js": b"var a=1;\n",
            "vendor/a.test.js": b"t = 1\n",
            "node_modules/m/foo_test.go": b"package m\n",
            "gen/x.spec.ts": b"// DO NOT EDIT\n",
            "test/data.py": MARKED,
        }
        base = self.files(files, "base")
        head = self.files({name: data + b"y\n" for name, data in files.items()}, "head")
        got = self.verdicts(base, head)
        for path in files:
            with self.subTest(path=path):
                self.assertEqual(got[path], "review")
        named = self.verdicts(base, head, [rule("vendor/pkg/test_*.py", skip="a vendored copy"),
                                           rule("gen/**", rules="r.md")])
        self.assertEqual(named["vendor/pkg/test_x.py"], "skip: a vendored copy (review.paths)")
        self.assertEqual(named["gen/x.spec.ts"], "review")
        self.assertEqual(named["vendor/a.test.js"], "review")

    def test_a_path_rule_skip_gives_its_reason(self):
        base = self.files({"docs/a.md": b"a\n", "src/b.py": b"b\n"}, "base")
        head = self.files({"docs/a.md": b"a2\n", "src/b.py": b"b2\n"}, "head")
        rules = [rule("docs/**", skip="prose", level=3), rule("docs/*.md", skip="project prose", level=4)]
        got = self.verdicts(base, head, rules)
        self.assertEqual(got, {"docs/a.md": "skip: project prose (review.paths)", "src/b.py": "review"})

    def test_a_binary_fixture_under_tests_is_skipped(self):
        base = self.files({"tests/fixtures/logo.png": NUL_TEXT, "tests/test_x.py": b"x = '\x00'\n"}, "base")
        head = self.files({"tests/fixtures/logo.png": NUL_TEXT + b"2", "tests/test_x.py": b"x = '\x00'\ny = 1\n"},
                          "head")
        self.assertEqual(self.verdicts(base, head),
                         {"tests/fixtures/logo.png": "skip: binary", "tests/test_x.py": "review"})

    def test_binary_detection_reads_a_bounded_prefix(self):
        big = b"0123456789\x00" + b"x" * (20 * 1024 * 1024)
        base = self.files({"big.dat": big}, "base")
        head = self.files({"big.dat": big + b"y"}, "head")
        seen = []
        real_read = review_scope._Blobs.read

        def counting(blobs, oid, cap):
            size, kept = real_read(blobs, oid, cap)
            seen.append((size, len(kept), cap))
            return size, kept

        with mock.patch.object(review_scope._Blobs, "read", counting):
            got = self.verdicts(base, head)
        self.assertEqual(got, {"big.dat": "skip: binary"})
        self.assertEqual(sorted(seen), [(len(big), 8000, 8000), (len(big) + 1, 8000, 8000)])

    def test_is_test_file_table(self):
        rows = [
            ("test_x.py", True), ("a/test_x.py", True), ("x_test.go", True), ("x.test.js", True),
            ("x.spec.ts", True), ("test/a.txt", True), ("tests/a/b.bin", True), ("src/__tests__/a.js", True),
            ("spec/a.rb", True), ("latest.py", False), ("contest.py", False), ("testing/a.py", False),
            ("tests_x/a.py", False), ("a/tests", False), ("Test_x.py", False), ("xtest.py", False),
            ("src/protest_x.py", False),
        ]
        for path, expected in rows:
            with self.subTest(path=path):
                self.assertIs(review_scope.is_test_file(path), expected)


class BatchReaderTests(GitCase):
    """The `git cat-file --batch` reader refuses every answer it does not expect, and has its own time bound."""

    def fake_batch(self, script):
        """Popen of the reader starts `script` (Python) in place of git."""
        def start(argv, *args, **kwargs):
            if kwargs.get("stdin") is not subprocess.PIPE:
                return REAL_POPEN(argv, *args, **kwargs)
            return REAL_POPEN([sys.executable, "-I", "-c", script], *args, **kwargs)

        return mock.patch.object(review_scope.subprocess, "Popen", start)

    def test_unexpected_answers_refuse(self):
        self.write("a.py", b"a = 1\n")
        self.commit("one")
        oid = self.git("rev-parse", "HEAD:a.py")
        other = "0" * 40
        reply = "import sys\nsys.stdin.readline()\nsys.stdout.buffer.write(%r)\nsys.stdout.flush()\n"
        rows = [
            ("missing", reply % ("%s missing\n" % oid).encode("ascii"), "printed an unexpected header"),
            ("a tree", reply % ("%s tree 3\nabc\n" % oid).encode("ascii"), "printed an unexpected header"),
            ("another id", reply % ("%s blob 3\nabc\n" % other).encode("ascii"), "printed an unexpected header"),
            ("no size", reply % ("%s blob x\nabc\n" % oid).encode("ascii"), "printed an unexpected header"),
            ("four fields", reply % ("%s blob 3 4\nabc\n" % oid).encode("ascii"), "printed an unexpected header"),
            ("short blob", reply % ("%s blob 10\nabc" % oid).encode("ascii"), "stopped inside a blob"),
            ("no line end", reply % ("%s blob 3\nabcX" % oid).encode("ascii"), "printed no line end after a blob"),
        ]
        for label, script, reason in rows:
            with self.subTest(label), self.fake_batch(script):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    with review_scope._Blobs(self.repo) as blobs:
                        blobs.read(oid, 10)
                self.assertIn(reason, str(caught.exception))
        with review_scope._Blobs(self.repo) as blobs:
            self.assertEqual(blobs.read(oid, 3), (6, b"a ="))
            with self.assertRaises(review_scope.ScopeError) as caught:
                blobs.read("HEAD:a.py", 10)
        self.assertIn("not an object id", str(caught.exception))

    def test_a_reader_that_never_answers_is_stopped(self):
        self.write("a.py", b"a = 1\n")
        self.commit("one")
        oid = self.git("rev-parse", "HEAD:a.py")
        started = time.monotonic()
        with self.fake_batch("import time\ntime.sleep(20)\n"), mock.patch.object(review_scope, "GIT_TIMEOUT", 1):
            with self.assertRaises(review_scope.ScopeError) as caught:
                with review_scope._Blobs(self.repo) as blobs:
                    blobs.read(oid, 10)
        self.assertIn("git cat-file took over 1 s", str(caught.exception))
        self.assertLess(time.monotonic() - started, 10)

    def test_a_git_failure_or_an_unexpected_tree_refuses(self):
        base, head = self.two_commits()

        def fake_tree(result):
            def run(argv, *args, **kwargs):
                if "ls-tree" not in argv:
                    return REAL_RUN(argv, *args, **kwargs)
                code, stdout = result
                return subprocess.CompletedProcess(argv, code, stdout, b"")

            return mock.patch.object(review_scope.subprocess, "run", run)

        rows = [
            ((128, b""), "git ls-tree failed with exit 128"),
            ((0, b"100644 blob " + b"0" * 40 + b"\ta.py\0"), "git ls-tree printed an unexpected entry"),
            ((0, b"100644 blob " + b"0" * 40 + b" 6\0"), "git ls-tree printed an unexpected entry"),
        ]
        for result, reason in rows:
            with self.subTest(reason=reason), fake_tree(result):
                with self.assertRaises(review_scope.ScopeError) as caught:
                    review_scope.scope(self.repo, base, head)
                self.assertIn(reason, str(caught.exception))


class GlobTests(unittest.TestCase):
    def test_glob_match_table(self):
        rows = [
            ("**/*.py", "a.py", True), ("**/*.py", "a/b/c.py", True), ("src/**/x.py", "src/x.py", True),
            ("src/**/x.py", "src/a/b/x.py", True), ("src/**", "src/a/b.py", True), ("**", "a/b", True),
            ("*.py", "a/b.py", False), ("a/*/c", "a/b/c", True), ("a/*/c", "a/b/d/c", False),
            ("a?c", "abc", True), ("a?c", "a/c", False), ("a?c", "ac", False), ("*.PY", "a.py", False),
            ("A.py", "a.py", False), ("*.min.js", "app.min.js", True), ("[ab].py", "a.py", False),
            ("[ab].py", "[ab].py", True), ("a*b*c", "aXbYc", True), ("a*b", "ab", True), ("a*b", "abc", False),
            ("src/**/x.py", "lib/x.py", False), ("docs/*.md", "docs/a.md", True),
        ]
        for pattern, path, expected in rows:
            with self.subTest(pattern=pattern, path=path):
                self.assertIs(review_scope.glob_match(pattern, path), expected)

    def run_bounded(self, pattern, path):
        result = []
        worker = threading.Thread(target=lambda: result.append(review_scope.glob_match(pattern, path)), daemon=True)
        started = time.monotonic()
        worker.start()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "glob_match still running after 5 s: %r" % pattern[:40])
        self.assertLess(time.monotonic() - started, 1.0)
        return result[0]

    def test_glob_match_is_bounded(self):
        segments = "/".join(["a"] * 40)
        self.assertFalse(self.run_bounded("**/" * 40 + "x", segments + "/y"))
        self.assertTrue(self.run_bounded("**/" * 40 + "x", segments + "/x"))
        self.assertFalse(self.run_bounded("*a" * 40 + "b", "a" * 200))
        self.assertTrue(self.run_bounded("*a" * 40 + "b", "a" * 200 + "b"))


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


class ProjectRulesTests(PathRuleCase):
    """D61: level 4 is read at base, by object id, and handed out as files."""

    def base_and_out(self, head_files, paths=None):
        paths = paths or {"src/**": {"rules": "docs/rules.md"}}
        base = self.files({".claude/rigger.json": self.project(paths), "docs/rules.md": b"base rules\n",
                           "src/a.py": b"a = 1\n"}, "base")
        head = self.files(head_files, "head")
        out = self.tmp / "rules out"
        return base, head, out

    def test_project_entries_come_from_base(self):
        rows = [
            ("the config file", {".claude/rigger.json": self.project({"src/**": {"skip": "head says skip"}}),
                                 "src/a.py": b"a = 2\n"}, ".claude/rigger.json"),
            ("the rules file", {"docs/rules.md": b"head rules\n", "src/a.py": b"a = 2\n"}, "docs/rules.md"),
        ]
        for label, head_files, changed in rows:
            with self.subTest(label):
                self.git("checkout", "-q", "--orphan", "case-" + changed.replace("/", "-").strip("."))
                self.git("rm", "-rqf", "--ignore-unmatch", ".")
                base, head, out = self.base_and_out(head_files)
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo),
                                     "--rules-out", str(out))
                self.assertIn("review\tsrc/a.py", lines)
                self.assertIn("# review.paths: project entries read at base; %s changed in this diff" % changed,
                              lines)
                rules_lines = [line for line in lines if line.startswith("# rules ")]
                self.assertEqual(len(rules_lines), 1, lines)
                self.assertTrue(rules_lines[0].startswith("# rules src/** "), rules_lines)
                self.assertTrue(rules_lines[0].endswith(" (level 4)"), rules_lines)
                written = rules_lines[0][len("# rules src/** "):-len(" (level 4)")]
                self.same_file(written, out / "rules-1.md")
                self.assertEqual((out / "rules-1.md").read_bytes(), b"base rules\n")

    def test_rules_out_holds_the_base_text(self):
        base, head, out = self.base_and_out({"src/a.py": b"a = 2\n"})
        self.write("docs/rules.md", b"working tree rules\n")
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(out))
        self.assertEqual((out / "rules-1.md").read_bytes(), b"base rules\n")
        rules_lines = [line for line in lines if line.startswith("# rules ")]
        self.assertEqual(len(rules_lines), 1, lines)
        self.assertNotIn("docs/rules.md", rules_lines[0])
        self.assertFalse(any("changed in this diff" in line for line in lines), lines)
        without = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo))
        self.assertIn("# rules src/** (not written: no --rules-out) (level 4)", without)

    def test_project_entries_apply_without_a_snapshot(self):
        base = self.files({".claude/rigger.json": self.project({"gen/**": {"skip": "built"}}),
                           "gen/x.py": b"x = 1\n"}, "base")
        head = self.files({"gen/x.py": b"x = 2\n"}, "head")
        for extra in ((), ("--session", "no-such-session")):
            with self.subTest(extra=extra):
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), *extra)
                self.assertIn("skip: built (review.paths)\tgen/x.py", lines)
                self.assertIn("# review.paths: none (no snapshot)", lines)
                self.assertIn("# review-scope: files 1, review 0, skipped 1", lines)

    def test_rules_lines_name_matched_entries_only(self):
        paths = {"src/**": "docs/rules.md", "docs/*.txt": "docs/rules.md",
                 "gen/**": {"rules": "docs/rules.md", "skip": "built"}}
        base = self.files({".claude/rigger.json": self.project(paths), "docs/rules.md": b"r\n",
                           "src/a.py": b"a\n", "gen/g.py": b"g\n", "docs/unchanged.txt": b"u\n"}, "base")
        head = self.files({"src/a.py": b"a2\n", "gen/g.py": b"g2\n"}, "head")
        out = self.tmp / "out"
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(out))
        rules_lines = [line for line in lines if line.startswith("# rules ")]
        self.assertEqual([line.split(" ")[2] for line in rules_lines], ["src/**"], lines)
        self.assertEqual(sorted(os.listdir(str(out))), ["rules-1.md"])

    def test_level_four_rules_file_outside_the_repository_is_dropped(self):
        (self.tmp / "outside.md").write_bytes(b"outside\n")
        paths = {"a/**": "../outside.md", "b/**": "docs/../../outside.md", "c/**": (self.tmp / "outside.md").as_posix(),
                 "d/**": "docs\\rules.md", "e/**": "./docs/rules.md", "f/**": "docs//rules.md"}
        base = self.files({".claude/rigger.json": self.project(paths), "docs/rules.md": b"r\n",
                           "a/x.py": b"1\n", "b/x.py": b"1\n", "c/x.py": b"1\n", "d/x.py": b"1\n", "e/x.py": b"1\n",
                           "f/x.py": b"1\n"}, "base")
        head = self.files({name: b"2\n" for name in ("a/x.py", "b/x.py", "c/x.py", "d/x.py", "e/x.py", "f/x.py")})
        notices = []
        self.assertEqual(review_scope.path_rules(None, self.repo, base, notices), [])
        for glob in paths:
            reason = "outside the repository" if glob[0] in "abc" else "not a plain repository path"
            with self.subTest(glob=glob):
                self.assertIn("project entry %s dropped: rules file %s" % (glob, reason), notices)
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out",
                             str(self.tmp / "out"))
        self.assertEqual([line for line in lines if line.startswith("# rules ")], [])

    def test_level_four_rules_file_must_be_tracked_at_base(self):
        hostile = "docs/$(x); y.md"
        self.write(".claude/rigger.json", self.project({"a/**": "docs/untracked.md", "b/**": hostile,
                                                        "c/**": "docs/link.md", "d/**": "docs/big.md"}))
        self.write(hostile, b"hostile rules\n")
        self.write("docs/big.md", b"x" * (review_scope.TEXT_CAP + 1))
        for name in ("a/x.py", "b/x.py", "c/x.py", "d/x.py"):
            self.write(name, b"1\n")
        self.git("add", "-A")
        target = self.repo / "link-target.txt"
        target.write_bytes(b"docs/rules.md")
        link = self.git("hash-object", "-w", str(target))
        target.unlink()
        self.git("update-index", "--add", "--cacheinfo", "120000,%s,docs/link.md" % link)
        self.git("commit", "-q", "-m", "base")
        base = self.git("rev-parse", "HEAD")
        self.write("docs/untracked.md", b"untracked rules\n")
        for name in ("a/x.py", "b/x.py", "c/x.py", "d/x.py"):
            self.write(name, b"2\n")
            self.git("add", name)
        self.git("commit", "-q", "-m", "head")
        head = self.git("rev-parse", "HEAD")
        out = self.tmp / "out"
        notices = []
        kept = review_scope.path_rules(None, self.repo, base, notices)
        self.assertEqual([item.glob for item in kept], ["b/**"])
        self.assertIn("project entry a/** dropped: rules file not tracked at base", notices)
        batch = []
        with self.git_calls(batch_input=batch) as calls:
            lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(out))
        self.assertIn("# review.paths: project entry a/** dropped: rules file not tracked at base", lines)
        self.assertIn("# review.paths: project entry c/** dropped: rules file not a regular file at base", lines)
        self.assertIn("# review.paths: project entry d/** dropped: rules file over %d bytes" % review_scope.TEXT_CAP,
                      lines)
        rules_lines = [line for line in lines if line.startswith("# rules ")]
        self.assertEqual(len(rules_lines), 1, lines)
        self.assertTrue(rules_lines[0].startswith("# rules b/** "), rules_lines)
        self.assertEqual((out / "rules-1.md").read_bytes(), b"hostile rules\n")
        self.assertTrue(calls)
        for argv, _env in calls:
            with self.subTest(argv=argv[1:]):
                for element in argv:
                    self.assertNotIn("$(x)", element)
                    self.assertNotIn("untracked", element)
                    self.assertNotIn("rules.md", element)
                if "ls-tree" in argv:
                    self.assertEqual(argv[-1], "--")
        fed = b"".join(batch).split(b"\n")
        self.assertEqual(fed[-1], b"")
        self.assertTrue(fed[:-1])
        for line in fed[:-1]:
            with self.subTest(line=line):
                self.assertRegex(line.decode("ascii", "replace"), r"\A[0-9a-f]{40}([0-9a-f]{24})?\Z")

    def test_level_four_entries_are_validated(self):
        rows = [
            ("a bad entry", self.project({"a/**": {}, "b/**": {"skip": "kept"}}),
             "config base:.claude/rigger.json review.paths a/** ignored: needs rules or skip", {"b/x.py"}),
            ("a line break in a reason", self.project({"b/**": {"skip": "x\nreview\tb/x.py"}}),
             "review.paths b/** ignored: skip holds a line break or control character", set()),
            ("not JSON", b"{nope", "not valid JSON", set()),
            ("schema 2", b'{"schema": 2, "review": {"paths": {"b/**": {"skip": "s"}}}}', "schema 2", set()),
            ("over the cap", self.project({"b/**": {"skip": "s"}}, pad="x" * review_scope.TEXT_CAP),
             "over %d bytes" % review_scope.TEXT_CAP, set()),
        ]
        for index, (label, body, notice, skipped) in enumerate(rows):
            with self.subTest(label):
                self.git("checkout", "-q", "--orphan", "case-%d" % index)
                self.git("rm", "-rqf", "--ignore-unmatch", ".")
                base = self.files({".claude/rigger.json": body, "a/x.py": b"1\n", "b/x.py": b"1\n"}, "base")
                head = self.files({"a/x.py": b"2\n", "b/x.py": b"2\n"}, "head")
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo))
                self.assertTrue(any(line.startswith("# review.paths: ") and notice in line for line in lines), lines)
                got = {line.split("\t")[1] for line in lines if line.startswith("skip: ")}
                self.assertEqual(got, skipped)

    def test_a_project_config_that_is_a_link_is_dropped(self):
        self.write("real.json", self.project({"a/**": {"skip": "s"}}))
        self.write("a/x.py", b"1\n")
        self.git("add", "-A")
        target = self.tmp / "target.txt"
        target.write_bytes(b"../real.json")
        link = self.git("hash-object", "-w", str(target))
        self.git("update-index", "--add", "--cacheinfo", "120000,%s,.claude/rigger.json" % link)
        self.git("commit", "-q", "-m", "base")
        base = self.git("rev-parse", "HEAD")
        notices = []
        self.assertEqual(review_scope.path_rules(None, self.repo, base, notices), [])
        self.assertEqual(notices, ["project config .claude/rigger.json dropped: not a regular file at base"])

    def test_rules_out_that_cannot_be_written_refuses(self):
        base, head, _out = self.base_and_out({"src/a.py": b"a = 2\n"})
        blocker = self.tmp / "a file"
        blocker.write_bytes(b"")
        code, text = self.cli("--base", base, "--head", head, "--repo", str(self.repo), "--rules-out", str(blocker))
        self.assertEqual(code, 2)
        self.assertTrue(text.startswith("review-scope: refused: rules could not be written to "), text)

    def test_validate_level_is_what_checks_level_four(self):
        base = self.files({".claude/rigger.json": self.project({"b/**": {"skip": "s"}}), "b/x.py": b"1\n"}, "base")
        with mock.patch.object(config, "validate_level", return_value=({}, ["rigger: config w refused"])) as check:
            notices = []
            self.assertEqual(review_scope.path_rules(None, self.repo, base, notices), [])
        self.assertEqual(check.call_args[0][1], 4)
        self.assertIn("config w refused", notices)


class SnapshotRulesTests(PathRuleCase):
    """D61: levels 2 and 3 from the session snapshot, an entry's level from its highest leaf."""

    def two(self, names):
        base = self.files({name: b"1\n" for name in names}, "base")
        head = self.files({name: b"2\n" for name in names}, "head")
        return base, head

    def test_an_entry_with_a_level_four_leaf_counts_as_level_four(self):
        (self.config_dir / "rigger").mkdir(parents=True)
        (self.config_dir / "rigger" / "r.md").write_bytes(b"r\n")
        l3 = {"schema": 1, "review": {"paths": {"a/**": {"rules": "r.md"}, "b/**": {"skip": "personal skip"}}}}
        l4 = {"schema": 1, "review": {"paths": {"a/**": {"skip": "session folder skip"},
                                                "c/**": {"skip": "from the snapshot"}}}}
        data = self.write_snapshot(l3=l3, l4=l4)
        base, head = self.two(("a/x.py", "b/x.py", "c/x.py"))
        rules = review_scope.path_rules(data, self.repo, base)
        self.assertEqual([(item.glob, item.skip, item.level) for item in rules], [("b/**", "personal skip", 3)])
        self.assertEqual(self.verdicts(base, head, rules),
                         {"a/x.py": "review", "b/x.py": "skip: personal skip (review.paths)", "c/x.py": "review"})

    def test_no_snapshot_applies_no_personal_or_company_rule(self):
        (self.config_dir / "rigger").mkdir(parents=True)
        (self.config_dir / "rigger" / "config.json").write_bytes(self.project({"a/**": {"skip": "personal"}}))
        self.write_snapshot(l3={"schema": 1, "review": {"paths": {"a/**": {"skip": "personal"}}}})
        base, head = self.two(("a/x.py",))
        self.assertEqual(review_scope.path_rules(None, self.repo, base), [])
        for extra in ((), ("--session", "another-session")):
            with self.subTest(extra=extra):
                lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), *extra)
                self.assertIn("review\ta/x.py", lines)
                self.assertIn("# review.paths: none (no snapshot)", lines)
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": SESSION}):
            lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo))
        self.assertIn("skip: personal (review.paths)\ta/x.py", lines)

    def test_snapshot_entries_resolve_per_level(self):
        module = self.tmp / "mods" / "company-a"
        (module / "rules").mkdir(parents=True)
        (module / "rules" / "c.md").write_bytes(b"c\n")
        (self.tmp / "mods" / "outside.md").write_bytes(b"o\n")
        (self.config_dir / "rigger").mkdir(parents=True)
        (self.config_dir / "rigger" / "p.md").write_bytes(b"p\n")
        l2 = [("company-a", {"review": {"paths": {"src/**": "rules/c.md", "out/**": "../outside.md"}}})]
        l3 = {"schema": 1, "review": {"paths": {"lib/**": "p.md", "gone/**": "missing.md"}}}
        self.write_snapshot(l2=l2, l3=l3, modules=[("company-a", module)])
        base, head = self.two(("src/a.py", "lib/b.py", "out/c.py", "gone/d.py"))
        lines = self.run_cli("--base", base, "--head", head, "--repo", str(self.repo), "--session", SESSION)
        rules_lines = {line.split(" ")[2]: line for line in lines if line.startswith("# rules ")}
        self.assertEqual(set(rules_lines), {"src/**", "lib/**"}, lines)
        self.assertTrue(rules_lines["src/**"].endswith(" (level 2)"))
        self.same_file(rules_lines["src/**"][len("# rules src/** "):-len(" (level 2)")], module / "rules" / "c.md")
        self.assertTrue(rules_lines["lib/**"].endswith(" (level 3)"))
        self.same_file(rules_lines["lib/**"][len("# rules lib/** "):-len(" (level 3)")],
                       self.config_dir / "rigger" / "p.md")
        self.assertIn("# review.paths: company entry out/** dropped: rules file outside the module", lines)
        self.assertIn("# review.paths: personal entry gone/** dropped: rules file not found", lines)

    def test_a_rules_file_resolves_by_the_level_that_set_it(self):
        module = self.tmp / "mods" / "company-a"
        (module / "rules").mkdir(parents=True)
        (module / "rules" / "c.md").write_bytes(b"c\n")
        (self.config_dir / "rigger" / "rules").mkdir(parents=True)
        (self.config_dir / "rigger" / "rules" / "c.md").write_bytes(b"personal c\n")
        l2 = [("company-a", {"review": {"paths": {"mix/**": {"rules": "rules/c.md"}}}}),
              ("company-b", {"review": {"paths": {"nomod/**": "rules/c.md"}}})]
        l3 = {"schema": 1, "review": {"paths": {"mix/**": {"skip": "personal"}}}}
        data = self.write_snapshot(l2=l2, l3=l3, modules=[("company-a", module)])
        base, _head = self.two(("mix/a.py",))
        notices = []
        rules = review_scope.path_rules(data, self.repo, base, notices)
        self.assertEqual([(item.glob, item.skip, item.level) for item in rules], [("mix/**", "personal", 3)])
        self.same_file(rules[0].rules, module / "rules" / "c.md")
        self.assertEqual(notices, ["company entry nomod/** dropped: module root unknown"])

    def test_snapshot_entries_are_checked_again(self):
        effective = {"review": {"paths": {"a/**": {}, "b/**": {"skip": "x\treview"}, "c/**": {"skip": "ok"},
                                          "d/**": {"skip": "no source"}}}}
        data = self.write_snapshot(l3={"schema": 1, "review": {"paths": {"a/**": {"skip": "s"}, "b/**": {"skip": "s"},
                                                                         "c/**": {"skip": "s"}}}},
                                   effective=effective)
        base, _head = self.two(("a/x.py",))
        notices = []
        rules = review_scope.path_rules(data, self.repo, base, notices)
        self.assertEqual([(item.glob, item.skip) for item in rules], [("c/**", "ok")])
        self.assertEqual(len(notices), 3, notices)
        self.assertIn("snapshot entry d/** dropped: its level is unknown", notices)

    def test_a_level_four_entry_replaces_a_lower_one_for_its_glob(self):
        l3 = {"schema": 1, "review": {"paths": {"src/**": {"skip": "personal"}, "lib/**": {"skip": "personal"}}}}
        data = self.write_snapshot(l3=l3)
        base = self.files({".claude/rigger.json": self.project({"src/**": "docs/r.md", "lib/**": None}),
                           "docs/r.md": b"r\n", "src/a.py": b"1\n", "lib/b.py": b"1\n"}, "base")
        head = self.files({"src/a.py": b"2\n", "lib/b.py": b"2\n"}, "head")
        rules = review_scope.path_rules(data, self.repo, base)
        self.assertEqual([(item.glob, item.rules, item.skip, item.level) for item in rules],
                         [("src/**", "docs/r.md", None, 4)])
        self.assertEqual(self.verdicts(base, head, rules), {"src/a.py": "review", "lib/b.py": "review"})


class CliTests(GitCase):
    def test_cli_lines_and_summary(self):
        base, head = self.two_commits()
        env = dict(os.environ)
        proc = REAL_RUN(
            [sys.executable, "-I", "-X", "utf8", SCRIPT, "--base", base],
            cwd=str(self.repo),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertNotIn(b"\r", proc.stdout)
        lines = proc.stdout.decode("utf-8").split("\n")
        expected = ["review\ta.py", "skip: deleted\tb.py", "review: binary added\tc.bin", "skip: deleted\td.py",
                    "review\te.py", "review\ttests/test_x.py", "review\t" + UNICODE_NAME,
                    "# review.paths: none (no snapshot)",
                    "# review-scope: files 7, review 5, skipped 2", ""]
        self.assertEqual(lines, expected)

    def test_cli_refuses_an_unknown_argument_and_a_missing_base(self):
        with self.no_git():
            for args in (["--head", "HEAD"], ["--base", "HEAD", "--bogus", "x"], ["--base"],
                         ["--base", "HEAD", "--base", "HEAD~1"]):
                with self.subTest(args=args):
                    code, text = self.cli(*args)
                    self.assertEqual(code, 2)
                    self.assertTrue(text.startswith("review-scope: refused: usage"), text)


if __name__ == "__main__":
    unittest.main()
