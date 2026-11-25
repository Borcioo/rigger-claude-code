"""review_scope: the list of files a review covers, and the refusals that guard the git calls.

python -I -X utf8 test_review_scope_basics.py
"""
import os
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from review_scope_case import (  # noqa: E402
    EXPECTED,
    REAL_RUN,
    SCRIPT,
    UNICODE_NAME,
    GitCase,
    review_scope,
)


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
