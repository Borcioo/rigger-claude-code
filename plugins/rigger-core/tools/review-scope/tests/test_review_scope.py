"""review_scope: the list of files a review covers, and the refusals that guard the git calls.

python -I -X utf8 test_review_scope.py
"""
import contextlib
import io
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

import review_scope  # noqa: E402

REAL_RUN = subprocess.run
UNICODE_NAME = "zażółć ą.py"
EXPECTED = [
    ("a.py", "review"),
    ("b.py", "skip: deleted"),
    ("c.bin", "review"),
    ("d.py", "skip: deleted"),
    ("e.py", "review"),
    ("tests/test_x.py", "review"),
    (UNICODE_NAME, "review"),
]


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
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(
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
    def git_calls(self):
        calls = []

        def spy(argv, *args, **kwargs):
            calls.append((list(argv), kwargs.get("env")))
            return REAL_RUN(argv, *args, **kwargs)

        with mock.patch.object(review_scope.subprocess, "run", spy):
            yield calls

    @contextlib.contextmanager
    def no_git(self):
        def refuse(argv, *args, **kwargs):
            raise AssertionError("git was started: %r" % (argv,))

        with mock.patch.object(review_scope.subprocess, "run", refuse):
            yield


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
            self.skipTest("case-sensitive file system at %s (on WSL set TMPDIR to a /mnt/<drive> folder)" % folder)
        return twin

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
        expected = ["review\ta.py", "skip: deleted\tb.py", "review\tc.bin", "skip: deleted\td.py", "review\te.py",
                    "review\ttests/test_x.py", "review\t" + UNICODE_NAME,
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
