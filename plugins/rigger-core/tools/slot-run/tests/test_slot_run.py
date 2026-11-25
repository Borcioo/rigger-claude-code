"""a command slot run with no shell, from the repository root, inside its bounds, its output checked.

python -I -X utf8 test_slot_run.py
"""
import io
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slot_run_case import (  # noqa: E402
    REAL_RUN,
    SCOPE_TEXT,
    SCRIPT,
    SESSION,
    SlotRunCase,
    command_row,
    remove_tree,
    same_path,
    slot_run,
)


class RunTests(SlotRunCase):
    def test_ok_run_names_the_output_file_and_prints_ok(self):
        self.write_snapshot([command_row(self.command("ok"))])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        with open(self.output_path(lines), "rb") as handle:
            self.assertEqual(json.loads(handle.read().decode("utf-8"))["findings"][0]["id"], "F1")

    def test_the_script_runs_from_its_command_line(self):
        self.write_snapshot([command_row(self.command("ok"))])
        proc = REAL_RUN([sys.executable, "-I", "-X", "utf8", SCRIPT, "reviewer", "--session", SESSION,
                         "--base", self.base, "--head", self.head], cwd=str(self.repo), stdin=subprocess.DEVNULL,
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        lines = proc.stdout.decode("utf-8").replace("\r\n", "\n").splitlines()
        self.addCleanup(remove_tree, os.path.dirname(self.output_path(lines)))
        self.assertEqual((proc.returncode, lines[-1], proc.stderr), (0, "slot-run: ok reviewer 1 findings", b""))

    def test_a_reviewer_that_copies_the_scope_file_passes(self):
        self.write_snapshot([command_row(self.command("copy-scope"))])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 0 findings"), lines)

    def test_scope_file_carries_review_paths_of_the_session(self):
        paths = {"a.py": {"skip": "reviewed elsewhere"}}
        config = {"effective": {"review": {"paths": paths}},
                  "sources": [[["review", "paths", "a.py", "skip"], "L3:personal"]]}
        self.write_snapshot([command_row(self.command("ok"))], config=config)
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        with open(os.path.join(os.path.dirname(self.output_path(lines)), "scope.txt"), "rb") as handle:
            scope = handle.read().decode("utf-8").splitlines()
        self.assertEqual(scope, ["skip: reviewed elsewhere (review.paths)\ta.py", "skip: deleted\tb.py", "skip: deleted\tc.py",
                                 "review\td.py"])

    def test_refused_output_is_refused(self):
        self.write_snapshot([command_row(self.command("bad-status"))])
        code, lines = self.run_slot()
        self.assertEqual(code, 2)
        self.assertEqual(lines[-1], "slot-run: refused: status: status is not one of DONE, PARTIAL, BLOCKED, "
                                    "NEEDS_CONTEXT")

    def test_nonzero_exit_refuses_even_with_a_valid_file(self):
        self.write_snapshot([command_row(self.command("nonzero"))])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: the command exited 3"), lines)

    def test_no_output_file_refuses(self):
        self.write_snapshot([command_row(self.command("no-out"))])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: the command wrote no output file"), lines)

    def test_output_file_over_the_cap_refuses(self):
        self.write_snapshot([command_row(self.command("big-out"))])
        with mock.patch.object(slot_run, "OUT_CAP", 10000):
            code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: the output file is over 10000 bytes"), lines)

    def test_same_base_and_head_refuses(self):
        self.write_snapshot([command_row(self.command("ok"))])
        code, lines = self.run_slot(["--session", SESSION, "--base", "HEAD", "--head", "HEAD"])
        self.assertEqual((code, lines), (2, ["slot-run: refused: nothing to review between HEAD and HEAD"]))
        self.assertFalse(os.path.exists(self.record))

    def test_the_command_runs_from_the_repository_root(self):
        (self.repo / "sub").mkdir()
        self.write_snapshot([command_row(self.command("ok"))])
        code, lines = self.run_slot(cwd=self.repo / "sub")
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        self.assertTrue(same_path(self.recorded()["cwd"], self.repo), self.recorded()["cwd"])

    def test_a_stray_git_folder_is_not_taken_for_the_root(self):
        """An empty `.git` folder in a subfolder is not a repository: git's own root disagrees, so nothing runs."""
        (self.repo / "sub" / ".git").mkdir(parents=True)
        self.write_snapshot([command_row(self.command("ok"))])
        sub = str(self.repo / "sub")
        code, lines = self.run_slot(["--session", SESSION, "--base", self.base, "--repo", sub])
        self.assertEqual((code, lines), (2, ["slot-run: refused: not a git repository: %s" % sub]))
        self.assertFalse(os.path.exists(self.record))

    def test_revision_and_repository_errors_refuse(self):
        outside = self.tmp / "norepo"
        outside.mkdir()
        rows = [
            ("a base that names no commit", ["--base", "no-such-rev"],
             "revision does not name a commit: no-such-rev"),
            ("a head that looks like an option", ["--base", self.base, "--head", "-p"],
             "revision starts with '-': -p"),
            ("a folder outside any repository", ["--base", self.base, "--repo", str(outside)],
             "not a git repository: %s" % outside),
        ]
        self.write_snapshot([command_row(self.command("ok"))])
        for row, args, reason in rows:
            with self.subTest(row=row):
                code, lines = self.run_slot(["--session", SESSION] + args)
                self.assertEqual((code, lines), (2, ["slot-run: refused: " + reason]))
                self.assertFalse(os.path.exists(self.record))

    def test_command_line_errors_refuse_with_the_usage(self):
        usage = "slot-run: refused: usage: slot_run.py <slot> --session <id> --base REV [--head REV] [--repo DIR]"
        rows = [
            ("no slot", []),
            ("an option for the slot", ["--repo", "--session", SESSION, "--base", "HEAD"]),
            ("an unknown option", ["reviewer", "--session", SESSION, "--base", "HEAD", "--out", "x"]),
            ("a repeated option", ["reviewer", "--session", SESSION, "--session", SESSION, "--base", "HEAD"]),
            ("an option without a value", ["reviewer", "--session", SESSION, "--base"]),
            ("no --session", ["reviewer", "--base", "HEAD"]),
            ("no --base", ["reviewer", "--session", SESSION]),
        ]
        self.write_snapshot([command_row(self.command("ok"))])
        for row, argv in rows:
            with self.subTest(row=row):
                out = io.BytesIO()
                code = slot_run.main(argv, stdout=out)
                self.assertEqual((code, out.getvalue().decode("utf-8")), (2, usage + "\n"))
        self.assertFalse(os.path.exists(self.record))

    def test_an_unexpected_error_is_a_refusal(self):
        self.write_snapshot([command_row(self.command("ok"))])
        with mock.patch.object(slot_run.review_scope, "scope", side_effect=RuntimeError("boom")):
            code, lines = self.run_slot()
        self.assertEqual((code, lines), (2, ["slot-run: refused: internal: RuntimeError: boom"]))
        self.assertFalse(os.path.exists(self.record))

    def test_no_shell_reads_the_arguments(self):
        hostile = ["; touch %s" % (self.tmp / "m1").as_posix(), "$(touch %s)" % (self.tmp / "m2").as_posix(), "&& x",
                   "a&b", "%PATH%", "^z"]  # no space: never quoted, so cmd.exe would read them
        self.write_snapshot([command_row(self.command("ok", *hostile))])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        self.assertEqual(self.recorded()["argv"][-len(hostile):], hostile)
        self.assertFalse((self.tmp / "m1").exists())
        self.assertFalse((self.tmp / "m2").exists())

    def test_placeholders_are_filled_once(self):
        self.write_snapshot([command_row(self.command("ok", "x{base}y{head}"))])
        code, lines = self.run_slot(["--session", SESSION, "--base", "HEAD~1"])
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        argv = self.recorded()["argv"]
        self.assertEqual(argv[argv.index("--base") + 1], self.base)
        self.assertEqual(argv[argv.index("--head") + 1], self.head)
        self.assertEqual(argv[-1], "x%sy%s" % (self.base, self.head))
        self.assertTrue(same_path(argv[argv.index("--out") + 1], self.output_path(lines)))
        with open(argv[argv.index("--scope") + 1], "rb") as handle:
            self.assertEqual(handle.read().decode("utf-8"), SCOPE_TEXT)
        values = {"base": "{out}", "head": "H", "out": "O", "scope": "S"}
        self.assertEqual(slot_run.fill(["{base}", "a{out}b{scope}", "{x}", "{{head}}", "{base"], values),
                         ["{out}", "aObS", "{x}", "{H}", "{base"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
