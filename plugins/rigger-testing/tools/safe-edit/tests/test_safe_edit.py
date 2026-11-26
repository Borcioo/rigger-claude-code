"""safe_edit.py: the in-place red run, one test per behaviour of the tool it replaces (safe-edit.ps1).

python -I -X utf8 test_safe_edit.py
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(HERE)
SCRIPT = os.path.join(TOOL_DIR, "safe_edit.py")
sys.path.insert(0, TOOL_DIR)

from safe_edit import platform_shell  # noqa: E402  (only to pick the quoting of a test's command)

POWERSHELL = platform_shell()[0] != "sh"
# A command that prints `# tests 1` and exits 1 when the mutation (`guard = off`) is in the file it is given.
CHECK = ("import sys; t = open(sys.argv[1], encoding='utf-8').read(); bad = 'guard = off' in t; "
         "print('# tests 1'); sys.exit(1 if bad else 0)")
KILL = "Stop-Process -Id (Get-Process -Id $PID).Parent.Id -Force" if POWERSHELL else "kill -9 $PPID"


def command(*argv):
    """One command string for the platform shell: `argv` quoted for it."""
    if POWERSHELL:
        return "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
    return " ".join(shlex.quote(part) for part in argv)


def python_command(code, *args):
    return command(sys.executable, "-I", "-c", code, *args)


class SafeEditTests(unittest.TestCase):
    def setUp(self):
        self.work = os.path.abspath(tempfile.mkdtemp(prefix="safe-edit-test-"))
        self.addCleanup(shutil.rmtree, self.work, True)

    def fixture(self, name, text, bom=False):
        path = os.path.join(self.work, name)
        with open(path, "wb") as handle:
            handle.write((b"\xef\xbb\xbf" if bom else b"") + text.encode("utf-8"))
        return path

    def read(self, path):
        with open(path, "rb") as handle:
            return handle.read()

    def text(self, path):
        return self.read(path).decode("utf-8")

    def safe_edit(self, *argv):
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", SCRIPT] + list(argv), stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        return proc.returncode, proc.stdout.decode("utf-8").splitlines()

    def red_args(self, path, find="guard = on", replace="guard = off", expect="# tests [1-9]", code=CHECK):
        return ["--path", path, "--find", find, "--replace", replace, "--run", python_command(code, path),
                "--expect-ran", expect]

    def test_edit_mode_replaces_one_match_and_keeps_lf(self):
        path = self.fixture("lf.txt", "a = 1;\nif (ok) {\n  go();\n}\n")
        code, lines = self.safe_edit("--path", path, "--find", "if (ok) {", "--replace", "if (true) {")
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["safe-edit: replaced 1 match(es) in %s (line endings and BOM kept)." % path])
        self.assertEqual(self.read(path), b"a = 1;\nif (true) {\n  go();\n}\n")

    def test_edit_mode_keeps_crlf_and_a_bom_and_maps_a_lf_in_find_to_crlf(self):
        path = self.fixture("crlf.txt", "naïve\r\nif (ok) {\r\n  go();\r\n", bom=True)
        code, _lines = self.safe_edit("--path", path, "--find", "if (ok) {\n  go();", "--replace",
                                      "if (ok) {\n  stop();")
        self.assertEqual(code, 0)
        self.assertEqual(self.read(path), b"\xef\xbb\xbf" + "naïve\r\nif (ok) {\r\n  stop();\r\n".encode("utf-8"))

    def test_a_crlf_in_find_matches_a_lf_file(self):
        path = self.fixture("lf-find-crlf.txt", "a\nb\n")
        code, _lines = self.safe_edit("--path", path, "--find", "a\r\nb", "--replace", "a\r\nc")
        self.assertEqual((code, self.read(path)), (0, b"a\nc\n"))

    def test_no_match_is_refused_and_the_file_is_untouched(self):
        path = self.fixture("nomatch.txt", "alpha\n")
        code, lines = self.safe_edit("--path", path, "--find", "beta", "--replace", "gamma")
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["safe-edit: REFUSED — expected 1 match(es) of --find in %s, found 0; "
                                 "nothing changed." % path])
        self.assertEqual(self.read(path), b"alpha\n")

    def test_two_matches_with_count_one_are_refused(self):
        path = self.fixture("twice.txt", "x x\n")
        code, lines = self.safe_edit("--path", path, "--find", "x", "--replace", "y")
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["safe-edit: REFUSED — expected 1 match(es) of --find in %s, found 2; "
                                 "nothing changed." % path])
        self.assertEqual(self.text(path), "x x\n")

    def test_count_names_the_number_of_matches_to_replace(self):
        path = self.fixture("count.txt", "x x\n")
        code, _lines = self.safe_edit("--path", path, "--find", "x", "--replace", "y", "--count", "2")
        self.assertEqual((code, self.text(path)), (0, "y y\n"))
        code, lines = self.safe_edit("--path", path, "--find", "y", "--replace", "z", "--count", "3")
        self.assertEqual((code, self.text(path)), (2, "y y\n"))
        self.assertIn("expected 3 match(es) of --find in %s, found 2" % path, lines[0])

    def test_red_run_failing_command_exits_0_restores_the_file_and_the_command_sees_the_mutation(self):
        path = self.fixture("red.txt", "guard = on\n")
        code, lines = self.safe_edit(*self.red_args(path))
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[1:], ["# tests 1", "safe-edit: restored %s (hash checked)." % path,
                                     "safe-edit: RED as expected — the command exited 1 with the mutation "
                                     "in place."])
        self.assertTrue(lines[0].startswith("safe-edit: mutation applied (1 match(es)) to %s; running: " % path))
        self.assertEqual(self.text(path), "guard = on\n")
        self.assertFalse(os.path.exists(path + ".safe-edit-backup"))

    def test_not_red_a_passing_command_exits_4_and_the_file_is_restored(self):
        path = self.fixture("green.txt", "guard = on\n")
        code, lines = self.safe_edit(*self.red_args(path, replace="guard = still on"))
        self.assertEqual(code, 4, lines)
        self.assertEqual(lines[-1], "safe-edit: NOT RED — the command passed with the mutation in place; "
                                    "the check did not catch it.")
        self.assertEqual(self.text(path), "guard = on\n")

    def test_expect_ran_matches_regardless_of_case(self):
        path = self.fixture("expectcase.txt", "guard = on\n")
        code, lines = self.safe_edit(*self.red_args(path, expect="# TESTS [1-9]"))
        self.assertEqual(code, 0, lines)

    def test_run_without_expect_ran_is_refused_before_the_file_is_touched(self):
        path = self.fixture("noexpect.txt", "guard = on\n")
        code, lines = self.safe_edit("--path", path, "--find", "guard = on", "--replace", "guard = off", "--run",
                                     python_command(CHECK, path))
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["safe-edit: REFUSED — --run needs --expect-ran '<regex>' matching a line only a "
                                 "test run prints (e.g. '# tests [1-9]'); without it a command that ran no test "
                                 "counts as red."])
        self.assertEqual(self.text(path), "guard = on\n")
        self.assertFalse(os.path.exists(path + ".safe-edit-backup"))

    def test_a_replacement_differing_only_in_case_is_a_mutation(self):
        path = self.fixture("case.txt", "flag = True\n")
        code, _lines = self.safe_edit("--path", path, "--find", "True", "--replace", "true")
        self.assertEqual((code, self.text(path)), (0, "flag = true\n"))

    def test_an_identical_find_and_replace_are_refused(self):
        path = self.fixture("same.txt", "flag = True\n")
        code, lines = self.safe_edit("--path", path, "--find", "True", "--replace", "True")
        self.assertEqual((code, lines), (2, ["safe-edit: REFUSED — --find and --replace are the same; that is "
                                             "no mutation."]))

    def test_an_empty_run_is_refused_with_or_without_expect_ran_and_the_file_is_untouched(self):
        path = self.fixture("emptyrun.txt", "guard = on\n")
        for extra in ([], ["--expect-ran", "x"]):
            with self.subTest(extra=extra):
                code, lines = self.safe_edit("--path", path, "--find", "guard = on", "--replace", "guard = off",
                                             "--run", "", *extra)
                self.assertEqual((code, lines), (2, ["safe-edit: REFUSED — --run is empty."]))
                self.assertEqual(self.text(path), "guard = on\n")

    def test_expect_ran_without_run_is_refused_and_the_file_is_untouched(self):
        path = self.fixture("expectonly.txt", "guard = on\n")
        code, lines = self.safe_edit("--path", path, "--find", "guard = on", "--replace", "guard = off",
                                     "--expect-ran", "x")
        self.assertEqual((code, lines), (2, ["safe-edit: REFUSED — --expect-ran needs --run."]))
        self.assertEqual(self.text(path), "guard = on\n")

    def test_no_evidence_expect_ran_never_matched_exits_3_and_the_file_is_restored(self):
        path = self.fixture("noevidence.txt", "guard = on\n")
        code, lines = self.safe_edit(*self.red_args(path, expect="# nothing printed like this"))
        self.assertEqual(code, 3, lines)
        self.assertEqual(lines[-1], "safe-edit: NO EVIDENCE — the output never matched --expect-ran "
                                    "'# nothing printed like this'; maybe no test ran (exit code 1).")
        self.assertEqual(self.text(path), "guard = on\n")

    def test_a_killed_run_leaves_a_backup_and_the_next_call_restores_the_file(self):
        path = self.fixture("killed.txt", "guard = on\nother edit kept\n")
        self.safe_edit("--path", path, "--find", "guard = on", "--replace", "guard = off", "--run", KILL,
                       "--expect-ran", "x")
        self.assertEqual(self.text(path), "guard = off\nother edit kept\n")  # the mutation stays after the kill
        self.assertTrue(os.path.exists(path + ".safe-edit-backup"))
        code, lines = self.safe_edit("--path", path, "--restore")
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["safe-edit: restored %s from a backup left by an earlier, interrupted run." % path,
                                 "safe-edit: nothing else to restore for %s." % path])
        self.assertEqual(self.text(path), "guard = on\nother edit kept\n")
        self.assertFalse(os.path.exists(path + ".safe-edit-backup"))

    def test_a_backup_whose_file_changed_since_is_refused_and_nothing_is_overwritten(self):
        path = self.fixture("drifted.txt", "guard = on\n")
        self.safe_edit("--path", path, "--find", "guard = on", "--replace", "guard = off", "--run", KILL,
                       "--expect-ran", "x")
        with open(path, "wb") as handle:
            handle.write(b"someone edited this\n")
        code, lines = self.safe_edit("--path", path, "--restore")
        self.assertEqual(code, 2)
        self.assertEqual(lines, ["safe-edit: REFUSED — %s.safe-edit-backup exists, but %s changed after that "
                                 "run. Compare the two, put the right content back by hand, then delete the "
                                 "backup." % (path, path)])
        self.assertEqual(self.text(path), "someone edited this\n")
        self.assertTrue(os.path.exists(path + ".safe-edit-backup"))

    def killed_run(self, name):
        path = self.fixture(name, "guard = on\n")
        self.safe_edit("--path", path, "--find", "guard = on", "--replace", "guard = off", "--run", KILL,
                       "--expect-ran", "x")
        self.assertTrue(os.path.exists(path + ".safe-edit-backup"))
        return path

    def test_a_backup_of_a_file_already_back_to_its_original_is_removed(self):
        path = self.killed_run("original.txt")
        with open(path, "wb") as handle:
            handle.write(b"guard = on\n")
        code, lines = self.safe_edit("--path", path, "--restore")
        self.assertEqual(code, 0)
        self.assertEqual(lines, ["safe-edit: removed a stale backup; %s already holds its original content." % path,
                                 "safe-edit: nothing else to restore for %s." % path])
        self.assertFalse(os.path.exists(path + ".safe-edit-backup"))

    def test_a_restore_whose_hash_does_not_match_the_backup_fails_and_keeps_the_backup(self):
        path = self.killed_run("tampered.txt")
        backup = path + ".safe-edit-backup"
        state = json.loads(self.read(backup).decode("utf-8"))
        state["originalSha256"] = "0" * 64
        with open(backup, "wb") as handle:
            handle.write(json.dumps(state).encode("utf-8"))
        code, lines = self.safe_edit("--path", path, "--restore")
        self.assertEqual(code, 1)
        self.assertEqual(lines, ["safe-edit: FAILED — RuntimeError: restored %s but its hash does not match "
                                 "the backup; the backup is kept at %s" % (path, backup)])
        self.assertTrue(os.path.exists(backup))

    def test_working_directory_is_the_cwd_of_the_command(self):
        path = self.fixture("cwd.txt", "guard = on\n")
        where = os.path.join(self.work, "elsewhere")
        os.mkdir(where)
        code, lines = self.safe_edit(*self.red_args(path, code="import os, sys; print('# tests 1'); "
                                                              "print('CWD=' + os.getcwd()); sys.exit(1)")
                                     + ["--working-directory", where])
        self.assertEqual(code, 0, lines)
        seen = [line[4:] for line in lines if line.startswith("CWD=")]
        self.assertEqual(len(seen), 1, lines)
        self.assertTrue(os.path.samefile(seen[0], where), (seen, where))

    def killed_write(self, path, argv, kill_on):
        """Run main in process; the `kill_on`-th os.replace is the kill: the temp file was written, nothing else."""
        import safe_edit
        calls = []
        real = os.replace

        class Killed(BaseException):
            pass

        def replace(src, dst):
            calls.append(dst)
            if len(calls) == kill_on:
                raise Killed()
            return real(src, dst)

        saved, os.replace = os.replace, replace
        try:
            with self.assertRaises(Killed):
                safe_edit._main(argv)
        finally:
            os.replace = saved
        return calls

    def run_args(self, path):
        return ["--path", path, "--find", "guard = on", "--replace", "guard = off", "--run",
                python_command(CHECK, path), "--expect-ran", "# tests [1-9]"]

    def test_a_kill_while_the_backup_is_written_leaves_no_truncated_backup(self):
        path = self.fixture("kill-backup.txt", "guard = on\n")
        self.killed_write(path, self.run_args(path), 1)
        self.assertEqual(self.text(path), "guard = on\n")
        self.assertFalse(os.path.exists(path + ".safe-edit-backup"))
        code, lines = self.safe_edit(*self.run_args(path))
        self.assertEqual(code, 0, lines)

    def test_a_kill_while_the_file_is_restored_leaves_a_whole_file_and_a_backup_that_restores_it(self):
        path = self.fixture("kill-restore.txt", "guard = on\n")
        calls = self.killed_write(path, self.run_args(path), 3)  # 1 backup, 2 mutation, 3 restore
        backup = path + ".safe-edit-backup"
        self.assertEqual(self.text(path), "guard = off\n")  # whole mutated file, not a partial write
        self.assertTrue(json.loads(self.read(backup).decode("utf-8"))["original"])
        code, lines = self.safe_edit("--path", path, "--restore")
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.text(path), "guard = on\n")
        self.assertFalse(os.path.exists(backup))

    def test_a_value_that_starts_with_a_dash_goes_with_an_equals_sign(self):
        path = self.fixture("dash.txt", "-old\n")
        code, lines = self.safe_edit("--path", path, "--find=-old", "--replace=-new")
        self.assertEqual((code, self.text(path)), (0, "-new\n"), lines)

    def test_an_expect_ran_that_is_no_regex_is_refused_before_the_file_is_touched(self):
        for bad in ("# tests [", "a{999999999999}"):
            with self.subTest(bad):
                path = self.fixture("badregex.txt", "guard = on\n")
                marker = os.path.join(self.work, "ran")
                code, lines = self.safe_edit(*self.red_args(path, expect=bad, code="open(%r, 'w').close()" % marker))
                self.assertEqual(code, 2, lines)
                self.assertTrue(lines[0].startswith("safe-edit: REFUSED — --expect-ran is not a usable regex: "), lines)
                self.assertEqual(self.text(path), "guard = on\n")
                self.assertFalse(os.path.exists(marker), "the command ran")

    def assert_link_refused(self, path, other):
        code, lines = self.safe_edit("--path", path, "--find", "on", "--replace", "off")
        self.assertEqual(code, 2, lines)
        self.assertEqual(lines, ["safe-edit: REFUSED — %s is a symlink or has other hard links; replacing it would "
                                 "cut the link. Edit it with the Edit tool, or name the real file." % path])
        self.assertEqual((self.text(path), self.text(other)), ("guard = on\n", "guard = on\n"))

    def test_a_hard_linked_file_is_refused_and_stays_linked(self):
        other = self.fixture("real.txt", "guard = on\n")
        path = os.path.join(self.work, "hard.txt")
        os.link(other, path)
        self.assert_link_refused(path, other)
        self.assertEqual(os.stat(path).st_nlink, 2)

    def test_a_symlink_is_refused_and_stays_a_link(self):
        other = self.fixture("target.txt", "guard = on\n")
        path = os.path.join(self.work, "soft.txt")
        try:
            os.symlink(other, path)
        except (OSError, NotImplementedError) as exc:
            self.skipTest("this account cannot make a symlink: %s" % exc)
        self.assert_link_refused(path, other)
        self.assertTrue(os.path.islink(path))

    def test_the_temp_files_of_a_killed_write_are_removed_at_the_next_start(self):
        path = self.fixture("leftover.txt", "guard = on\n")
        temps = [path + ".safe-edit-tmp", path + ".safe-edit-backup.safe-edit-tmp"]
        for temp in temps:
            with open(temp, "wb") as handle:
                handle.write(b"half")
        code, lines = self.safe_edit("--path", path, "--restore")
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[:2], ["safe-edit: removed %s, left by a killed write." % temp for temp in temps])
        self.assertEqual([temp for temp in temps if os.path.exists(temp)], [])
        self.assertEqual(self.text(path), "guard = on\n")

    def test_windows_without_pwsh_runs_the_command_through_sh(self):
        asked = []

        def which(name):
            asked.append(name)
            return None

        self.assertEqual(platform_shell(which=which, windows=True), ["sh", "-c"])
        self.assertEqual(platform_shell(which=lambda name: "found-pwsh", windows=True),
                         ["found-pwsh", "-NoProfile", "-Command"])
        self.assertEqual(platform_shell(which=which, windows=False), ["sh", "-c"])
        self.assertEqual(asked, ["pwsh"])

    def test_platform_shell_is_recorded_in_the_backup(self):
        path = self.fixture("shell.txt", "guard = on\n")
        seen = os.path.join(self.work, "backup-seen.json")
        copy = "import shutil, sys; shutil.copyfile(sys.argv[1] + '.safe-edit-backup', sys.argv[2])"
        args = ["--path", path, "--find", "guard = on", "--replace", "guard = off", "--run",
                python_command(copy + "; print('# tests 1'); sys.exit(1)", path, seen), "--expect-ran", "# tests 1"]
        code, lines = self.safe_edit(*args)
        self.assertEqual(code, 0, lines)
        shell = json.loads(self.read(seen).decode("utf-8"))["shell"]
        pwsh = shutil.which("pwsh") if os.name == "nt" else None
        if pwsh:
            self.assertEqual((os.path.basename(shell[0]).lower().split(".")[0], shell[1:]),
                             ("pwsh", ["-NoProfile", "-Command"]))
        else:
            self.assertEqual(shell, ["sh", "-c"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
