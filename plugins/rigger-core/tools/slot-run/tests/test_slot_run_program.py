"""slot_run: how the program of a command is resolved, the snapshot a slot is read from and the child environment.

python -I -X utf8 test_slot_run_program.py
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from slot_run_case import (  # noqa: E402
    BARE_OR_ABSOLUTE,
    NOT_FOUND,
    NT,
    REAL_POPEN,
    REAL_RUN,
    SESSION,
    SlotRunCase,
    command_row,
    inside,
    same_path,
    slot_run,
    slots,
    snapshot,
)


class ProgramTests(SlotRunCase):
    """Review Focus 11: a program a cloned repository plants is never run."""

    def setUp(self):
        super().setUp()
        self.name = "reviewer-cli"
        self.file = self.name + (".exe" if NT else "")
        git = shutil.which("git")
        self.assertIsNotNone(git)
        if NT:
            self.system = os.path.join(os.environ["SYSTEMROOT"], "System32")
            self.source = os.path.join(self.system, "whoami.exe")
        else:
            self.system = "/usr/bin"
            self.source = str(self.tmp / "planted.sh")
            with open(self.source, "wb") as handle:
                handle.write(b"#!/bin/sh\necho PLANTED\n")
            os.chmod(self.source, 0o755)
        self.base_path = [self.system, os.path.dirname(os.path.abspath(git))]
        self.planted = self.plant(self.repo)
        self.calls = []

    def plant(self, folder):
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / self.file
        shutil.copy(self.source, str(target))
        return str(target)

    def run_with_path(self, entries, program=None, cwd=None, args=None):
        self.write_snapshot([command_row(self.command("ok", program=program or self.name))])

        def recording_popen(*popen_args, **kwargs):
            argv = popen_args[0] if popen_args else kwargs["args"]
            self.calls.append((argv[0], kwargs.get("cwd")))
            return REAL_POPEN(*popen_args, **kwargs)

        with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(entries)}), \
                mock.patch.object(subprocess, "Popen", recording_popen):
            return self.run_slot(args, cwd=cwd)

    def reviewer_calls(self):
        return [call for call in self.calls if os.path.basename(call[0]).lower().startswith(self.name)]

    def test_program_planted_in_the_repository_is_never_run(self):
        old = os.getcwd()
        os.chdir(str(self.repo))
        try:  # known-answer control: the operating system's own lookup runs the planted copy from here
            path = os.pathsep.join([self.system] if NT else [".", self.system])
            control = REAL_RUN([self.name, "/?"] if NT else [self.name], env=dict(os.environ, PATH=path),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        finally:
            os.chdir(old)
        self.assertEqual(control.returncode, 0, control.stderr)
        if not NT:
            self.assertEqual(control.stdout, b"PLANTED\n")
        sub_copy = self.plant(self.repo / "sub")
        self.plant(self.repo / "bin")
        self.plant(self.tmp / "relbatch")
        # A batch file the lookup must not see either; `../relbatch` is a relative entry outside the repository.
        for folder in (self.repo, self.repo / "sub", self.repo / "bin", self.tmp / "relbatch"):
            (folder / (self.name + ".cmd")).write_bytes(b"@echo RAN\r\n")

        rows = [
            ("nothing on PATH", self.base_path, None, None, NOT_FOUND),
            ("empty, dot and relative entries", self.base_path + ["", ".", "sub", "../relbatch"], None, None,
             NOT_FOUND),
            ("the repository root as an entry", self.base_path + [str(self.repo)], None, None, NOT_FOUND),
            ("a repository folder as an entry, run from a subfolder", self.base_path + [str(self.repo / "bin")],
             None, self.repo / "sub", NOT_FOUND),
            ("the planted file by absolute path", self.base_path, self.planted, None,
             "slot-run: refused: %s resolves inside the repository" % self.planted),
            ("the copy in a subfolder by absolute path", self.base_path, sub_copy, None,
             "slot-run: refused: %s resolves inside the repository" % sub_copy),
            ("a relative name with a dot", self.base_path, "./" + self.name, None, BARE_OR_ABSOLUTE),
            ("a relative name with a folder", self.base_path, "sub/" + self.name, None, BARE_OR_ABSOLUTE),
        ]
        if NT:  # rooted but on no drive: it would resolve against the working directory's drive
            rows.append(("a rooted name with no drive", self.base_path, "/" + self.name, None, BARE_OR_ABSOLUTE))
        for row, entries, program, cwd, expected in rows:
            with self.subTest(row=row):
                self.calls = []
                code, lines = self.run_with_path(entries, program=program, cwd=cwd)
                self.assertEqual((code, lines), (2, [expected]))
                self.assertEqual(self.reviewer_calls(), [])

        with self.subTest(row="a copy outside the repository on PATH"):
            self.calls = []
            outside = self.plant(self.tmp / "outbin")
            self.run_with_path(self.base_path + [str(self.repo), str(self.tmp / "outbin")])
            self.assertEqual(len(self.reviewer_calls()), 1, self.calls)
            argv0, cwd = self.reviewer_calls()[0]
            self.assertTrue(same_path(argv0, outside), argv0)
            self.assertTrue(os.path.isabs(argv0))
            self.assertTrue(same_path(cwd, self.repo), cwd)

    @unittest.skipUnless(NT, "program extensions are a Windows lookup")
    def test_program_extensions_outside_the_repository(self):
        """A bare name given with `.exe`, and a `.com` program, are found and started outside the repository."""
        com_folder = self.tmp / "combin"
        com_folder.mkdir()
        com = str(com_folder / ("comtool" + ".com"))  # split: the sweep's host rule reads the joined name
        shutil.copy(self.source, com)  # a PE image starts whatever its extension
        exe = self.plant(self.tmp / "outbin")
        for row, program, expected in (("name.exe", self.file, exe), (".com", "comtool", com)):
            with self.subTest(row=row):
                self.calls = []
                code, lines = self.run_with_path(self.base_path + [str(self.tmp / "outbin"), str(com_folder)],
                                                 program=program)
                started = [call for call in self.calls if same_path(call[0], expected)]
                self.assertEqual(len(started), 1, self.calls)
                self.assertTrue(lines[-1].startswith("slot-run: refused: the command exited "), lines)

    def test_working_directory_entries_are_not_searched(self):
        """Started from a folder outside the repository, with --repo: that folder's copy is never run."""
        elsewhere = self.tmp / "elsewhere"
        copy = self.plant(elsewhere)
        (elsewhere / (self.name + ".cmd")).write_bytes(b"@echo RAN\r\n")
        args = ["--session", SESSION, "--base", self.base, "--head", self.head, "--repo", str(self.repo)]
        for row, entries, program, expected in (
                ("the folder as an entry", self.base_path + [str(elsewhere)], None, NOT_FOUND),
                ("the copy by absolute path", self.base_path, copy,
                 "slot-run: refused: %s resolves inside the repository" % copy)):
            with self.subTest(row=row):
                self.calls = []
                code, lines = self.run_with_path(entries, program=program, cwd=elsewhere, args=args)
                self.assertEqual((code, lines), (2, [expected]))
                self.assertEqual(self.reviewer_calls(), [])

    def test_a_linked_path_entry_into_the_repository_refuses(self):
        link = self.tmp / "link"
        target = self.repo / "bin"
        self.plant(target)
        if NT:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(str(target), str(link))
        through_link = str(link / self.file)
        for row, entries, program, expected in (
                ("the linked folder as an entry", self.base_path + [str(link)], None, NOT_FOUND),
                ("the program by absolute path through the link", self.base_path, through_link,
                 "slot-run: refused: %s resolves inside the repository" % through_link)):
            with self.subTest(row=row):
                self.calls = []
                code, lines = self.run_with_path(entries, program=program)
                self.assertEqual((code, lines), (2, [expected]))
                self.assertEqual(self.reviewer_calls(), [])

    @unittest.skipUnless(NT, "a batch file runs through cmd.exe only on Windows")
    def test_batch_file_refuses(self):
        folder = self.tmp / "batch"
        folder.mkdir()
        for name in ("tool.cmd", "TOOL2.BAT", "only.cmd"):
            (folder / name).write_bytes(b"@echo RAN\r\n")
        rows = [
            ("absolute .cmd", str(folder / "tool.cmd"), str(folder / "tool.cmd")),
            ("absolute .BAT", str(folder / "TOOL2.BAT"), str(folder / "TOOL2.BAT")),
            ("absolute .cmd with a trailing space", str(folder / "tool.cmd") + " ", str(folder / "tool.cmd") + " "),
            ("bare name with only a batch file on PATH", "only", "only"),
            ("bare name with the batch extension", "only.cmd", "only.cmd"),
        ]
        for row, program, shown in rows:
            with self.subTest(row=row):
                self.calls = []
                code, lines = self.run_with_path(self.base_path + [str(folder)], program=program)
                self.assertEqual((code, lines), (2, ["slot-run: refused: %s is a batch file; name the program it "
                                                     "starts (for an npm tool: node <script>)" % shown]))
                self.assertEqual([call for call in self.calls if inside(call[0], folder)], [])

    def other_spelling_rows(self, spell):
        """(row, program path, cwd, args) for a program in the repository and one in the working directory (started
        there with --repo), each named through `spell(folder)`, another spelling of the folder that holds it."""
        elsewhere = self.tmp / "elsewhere"
        self.plant(elsewhere)
        repo_args = ["--session", SESSION, "--base", self.base, "--head", self.head, "--repo", str(self.repo)]
        return [("the repository", os.path.join(spell(self.repo), self.file), None, None),
                ("the working directory", os.path.join(spell(elsewhere), self.file), elsewhere, repo_args)]

    def assert_refused_by_identity(self, rows):
        for row, program, cwd, args in rows:
            with self.subTest(row=row):
                self.assertTrue(os.path.isfile(program), program)
                self.calls = []
                code, lines = self.run_with_path(self.base_path, program=program, cwd=cwd, args=args)
                self.assertEqual((code, lines), (2, ["slot-run: refused: %s resolves inside the repository" % program]))
                self.assertEqual(self.reviewer_calls(), [])

    def test_absolute_program_spelled_in_another_case_is_refused(self):
        """On a case-insensitive file system (macOS by default, WSL's /mnt/<drive>, Windows) the repository's or the
        working directory's name in the other case is the same folder."""
        def twin(folder):
            spelled = folder.parent / folder.name.swapcase()
            if not spelled.is_dir() or not os.path.samefile(str(spelled), str(folder)):
                reason = "case-sensitive file system at %s (on WSL set TMPDIR to a /mnt/<drive> folder)" % folder
                if sys.platform == "darwin":  # APFS is case-insensitive by default: a skip would hide the proof
                    self.fail(reason)
                self.skipTest(reason)
            return str(spelled)
        self.assert_refused_by_identity(self.other_spelling_rows(twin))

    @unittest.skipUnless(NT, "the admin share is a Windows spelling")
    def test_absolute_program_through_the_admin_share_is_refused(self):
        """`\\\\localhost\\<drive>$\\…` names the same file as the drive path, though no spelling or real path
        matches; skipped when the share cannot be opened, failed instead when CI is set."""
        def share(folder):
            drive, rest = os.path.splitdrive(os.path.abspath(str(folder)))
            probe = "\\\\localhost\\%s$\\" % drive[:1]
            if not os.path.isdir(probe):
                reason = "the admin share %s cannot be opened" % probe
                if os.environ.get("CI"):
                    self.fail(reason)
                self.skipTest(reason)
            return probe + rest.lstrip("\\")
        self.assert_refused_by_identity(self.other_spelling_rows(share))

    def test_a_repository_that_cannot_be_read_refuses_every_program(self):
        """Without the repository's identity another spelling of it cannot be ruled out."""
        outside = self.plant(self.tmp / "outbin")
        with self.assertRaises(slot_run.Refusal) as caught:
            slot_run.resolve_program(outside, self.tmp / "gone")
        self.assertEqual(str(caught.exception), "%s resolves inside the repository" % outside)
        self.assertTrue(same_path(slot_run.resolve_program(outside, self.repo), outside))  # control

    def test_absolute_path_that_is_not_a_program_file_refuses(self):
        script = self.tmp / "tool.py"
        script.write_bytes(b"print(1)\n")  # no execute bit on POSIX
        missing = str(self.tmp / ("missing" + (".exe" if NT else "")))
        rows = [("missing", missing, "slot-run: refused: %s is not a program file" % missing),
                ("a script", str(script), "slot-run: refused: %s is %s" % (
                    script, "not an .exe or .com program" if NT else "not a program file"))]
        for row, program, expected in rows:
            with self.subTest(row=row):
                code, lines = self.run_with_path(self.base_path, program=program)
                self.assertEqual((code, lines), (2, [expected]))


class SnapshotTests(SlotRunCase):
    def assert_refused_without_a_run(self, args, reason):
        code, lines = self.run_slot(args)
        self.assertEqual((code, lines), (2, ["slot-run: refused: " + reason]))
        self.assertFalse(os.path.exists(self.record))

    def test_old_or_missing_snapshot_refuses(self):
        """Review Focus 8: a missing, format-1 or unreadable snapshot, or an unusable id, runs nothing."""
        missing = "no snapshot for this session; start a new session"
        with self.subTest(row="no file"):
            self.assert_refused_without_a_run(["--session", SESSION, "--base", self.base], missing)
        self.write_snapshot([command_row(self.command("ok"))])
        path = snapshot.snapshot_path(self.config, SESSION)
        data = json.loads(path.read_bytes().decode("utf-8"))
        with self.subTest(row="format 1"):
            data["format"] = 1
            path.write_bytes(json.dumps(data).encode("utf-8"))
            self.assert_refused_without_a_run(["--session", SESSION, "--base", self.base], missing)
        with self.subTest(row="slots the reader refuses"):
            data["format"] = snapshot.SNAPSHOT_FORMAT
            data["slots"]["rows"][0]["source"] = "somewhere"
            path.write_bytes(json.dumps(data).encode("utf-8"))
            self.assert_refused_without_a_run(["--session", SESSION, "--base", self.base], missing)
        for session in ("../x", "a/b", "", "s" * 300):
            with self.subTest(row="id", session=session[:20]):
                self.assert_refused_without_a_run(["--session", session, "--base", self.base],
                                                  "session id is not usable")

    def test_slot_that_is_not_a_command_refuses_with_the_dispatch_hint(self):
        self.write_snapshot([command_row("rigger-core:reviewer", kind="agent")])
        self.assert_refused_without_a_run(["--session", SESSION, "--base", self.base],
                                          "slot reviewer is agent; dispatch its target instead")

    def test_empty_slot_refuses(self):
        for row, rows in (("not filled", [command_row(None, kind=None, filled=False)]), ("no row", [])):
            with self.subTest(row=row):
                self.write_snapshot(rows)
                self.assert_refused_without_a_run(["--session", SESSION, "--base", self.base],
                                                  "slot reviewer is empty")

    def test_untrusted_project_command_in_the_snapshot_refuses(self):
        self.write_snapshot([command_row(self.command("ok"), source="project")])
        self.assert_refused_without_a_run(["--session", SESSION, "--base", self.base],
                                          "the project's command for reviewer is not trusted")
        self.write_snapshot([command_row(self.command("ok"), source="project")], trusted=["reviewer"])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)

    def test_a_personal_command_with_a_project_model_runs(self):
        """D53: a level-3 command with a level-4 model stays `personal` and needs no trust."""
        self.write_snapshot([command_row(self.command("ok"), source="personal", model="small-model")])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)


class EnvironmentTests(SlotRunCase):
    def test_strip_env_compares_names_in_upper_case(self):
        environ = {"CLAUDE_X": "1", "claude_y": "1", "HARNESS_Z": "1", "harness_w": "1", "RIGGER_MODULE_DIRS": "1",
                   "rigger_module_dirs": "1", "GIT_DIR": "1", "git_work_tree": "1", "RIGGER_OTHER": "1",
                   "ANTHROPIC_Q": "1", "PATH": "p"}
        self.assertEqual(slot_run.strip_env(environ), {"RIGGER_OTHER": "1", "ANTHROPIC_Q": "1", "PATH": "p"})

    def test_environment_is_stripped(self):
        planted = {"CLAUDE_X": "1", "claude_y": "1", "HARNESS_Z": "1", "RIGGER_MODULE_DIRS": "1",
                   "RIGGER_OTHER": "1", "ANTHROPIC_Q": "1", "GIT_TEST_X": "1"}
        self.write_snapshot([command_row(self.command("ok"))])
        with mock.patch.dict(os.environ, planted):
            code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        names = set(name.upper() for name in self.recorded()["env"])
        seen = names & set(name.upper() for name in list(planted) + ["CLAUDE_CONFIG_DIR", "GIT_CONFIG_GLOBAL"])
        self.assertEqual(seen, {"RIGGER_OTHER", "ANTHROPIC_Q"})
        self.assertIn("PATH", names)


if __name__ == "__main__":
    unittest.main(verbosity=2)
