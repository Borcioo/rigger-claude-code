"""slot_run: the child's program lookup, modules and environment.

python -I -X utf8 test_slot_run_child.py
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
    FAKE,
    NODE_REVIEWER,
    NT,
    REAL_RUN,
    SAFE_PATH,
    SESSION,
    SETTING,
    SlotRunCase,
    command_row,
    inside,
    make_link,
    slot_run,
)


class ChildLookupTests(SlotRunCase):
    """I1: a program the command starts by bare name is never one the repository or the working directory holds."""

    def setUp(self):
        super().setUp()
        git = shutil.which("git")
        self.assertTrue(git and os.path.isabs(git))
        system = os.path.join(os.environ["SYSTEMROOT"], "System32") if NT else "/usr/bin"
        self.base_path = [os.path.dirname(git), system]
        self.elsewhere = self.tmp / "elsewhere"
        # The fixture's git_in runs bare git from the repository: never call it once the plant is there.
        self.marker = self.plant(self.repo)
        self.plant(self.elsewhere)

    def plant(self, folder):
        """A `git` in `folder` that a run can tell apart from the real one by its --version output."""
        folder.mkdir(parents=True, exist_ok=True)
        if NT:  # a copy of python.exe: `git --version` prints "Python 3..."
            shutil.copy2(sys.executable, str(folder / "git.exe"))
            for dll in Path(os.path.dirname(sys.executable)).glob("*.dll"):
                shutil.copy2(str(dll), str(folder / dll.name))
            return "Python"
        plant = folder / "git"
        plant.write_bytes(b"#!/bin/sh\necho planted\n")
        os.chmod(str(plant), 0o755)
        return "planted"

    def helper_result(self):
        with open(self.record + ".helper", "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def control(self, argv):
        """Known answer: the same reviewer started from the repository root with only strip_env runs the plant."""
        env = slot_run.strip_env(os.environ)
        env["PATH"] = os.pathsep.join(([] if NT else ["."]) + self.base_path)
        self.assertNotIn(SETTING.upper(), [name.upper() for name in env])
        proc = REAL_RUN(argv, cwd=str(self.repo), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertIn(self.marker, self.helper_result().get("stdout", ""))
        os.remove(self.record + ".helper")

    def rows(self):
        link = make_link(self.tmp / "repo-link", self.repo)
        with_repo = ["--session", SESSION, "--base", self.base, "--head", self.head, "--repo", str(self.repo)]
        return [  # (row, PATH entries, the folder slot_run starts from, its arguments)
            ("the inherited PATH", self.base_path, None, None),
            ("a dot entry first", ["."] + self.base_path, None, None),
            ("an empty entry first", [""] + self.base_path, None, None),
            ("the repository root first", [str(self.repo)] + self.base_path, None, None),
            ("a link into the repository first", [str(link)] + self.base_path, None, None),
            ("the working directory first, with --repo", [str(self.elsewhere)] + self.base_path, self.elsewhere,
             with_repo),
        ]

    def run_rows(self, expected_last):
        for row, entries, cwd, args in self.rows():
            with self.subTest(row=row):
                with mock.patch.dict(os.environ, {"PATH": os.pathsep.join(entries)}):
                    code, lines = self.run_slot(args, cwd=cwd)
                self.assertEqual(lines[-1], expected_last, lines)
                result = self.helper_result()
                os.remove(self.record + ".helper")
                self.assertTrue(result.get("stdout", "").startswith("git version"), result)

    def test_a_python_reviewer_never_starts_the_planted_git(self):
        scope = self.tmp / "scope.txt"
        scope.write_bytes(b"")
        self.control([sys.executable, FAKE, "helper", "--record", self.record, "--helper", "git",
                      "--out", str(self.tmp / "out.json"), "--scope", str(scope), "--base", self.base])
        self.write_snapshot([command_row(self.command("helper", "--helper", "git"))])
        self.run_rows("slot-run: ok reviewer 1 findings")

    def test_a_node_reviewer_never_starts_the_planted_git(self):
        node = shutil.which("node")
        if node is None or (NT and not node.lower().endswith(".exe")):
            self.skipTest("no node program on PATH")
        version = REAL_RUN([node, "--version"], stdout=subprocess.PIPE, timeout=60).stdout.decode("ascii").strip()
        sys.stderr.write("node %s %s ... " % (node, version))
        script = self.tmp / "reviewer.js"
        script.write_bytes(NODE_REVIEWER.encode("utf-8"))
        argv = [node, str(script), self.record + ".helper", "{out}"]  # the script writes no {out} file
        self.control(argv)
        self.write_snapshot([command_row(argv)])
        self.run_rows("slot-run: refused: the command wrote no output file")


class ChildModuleTests(SlotRunCase):
    """F2: a Python command started from the repository root never imports a module the repository plants."""

    def setUp(self):
        super().setUp()
        self.marker = self.tmp / "planted-module-ran"
        (self.repo / "review_tool.py").write_bytes(("open(%r, 'w').close()\n" % str(self.marker)).encode("utf-8"))

    def control(self, argv):
        """Known answer: the same argv from the repository root with only strip_env imports the plant."""
        env = {name: value for name, value in slot_run.strip_env(os.environ).items()
               if name.upper() != "PYTHONSAFEPATH"}
        proc = REAL_RUN(argv, cwd=str(self.repo), env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                        stderr=subprocess.STDOUT, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout)
        self.assertTrue(self.marker.exists(), "the control did not import the planted module")
        self.marker.unlink()

    def test_python_never_imports_a_module_planted_in_the_repository_root(self):
        version = REAL_RUN([sys.executable, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
                           stdout=subprocess.PIPE, timeout=60).stdout.decode("ascii").strip()
        sys.stderr.write("python %s ... " % version)
        if tuple(int(part) for part in version.split(".")) < (3, 11):
            self.skipTest("python %s ignores PYTHONSAFEPATH (3.11+): the documented known limit" % version)
        rows = [("python -m", [sys.executable, "-m", "review_tool"], None),
                ("python -c", [sys.executable, "-c", "import review_tool"], None),
                ("python -m, inherited PYTHONPATH '.'", [sys.executable, "-m", "review_tool"], "."),
                ("python -m, inherited PYTHONPATH with a trailing separator", [sys.executable, "-m", "review_tool"],
                 str(self.tmp / "outlib") + os.pathsep)]
        for row, argv, inherited in rows:
            with self.subTest(row=row), mock.patch.dict(os.environ, {"PYTHONPATH": inherited} if inherited else {}):
                self.control(argv)
                self.write_snapshot([command_row(argv + ["{out}"])])
                code, lines = self.run_slot()
                self.assertFalse(self.marker.exists(), "the planted module ran")
                self.assertEqual((code, lines[-1]), (2, "slot-run: refused: the command exited 1"), lines)
                console = Path(self.output_path(lines)).parent / "console.txt"
                self.assertIn(b"No module named", console.read_bytes())

    def test_python_still_imports_its_own_modules_and_a_missing_one_still_errors(self):
        rows = [("a standard module", [sys.executable, "-c", "import json"], "the command wrote no output file"),
                ("a missing module", [sys.executable, "-m", "no_such_module_xyz"], "the command exited 1")]
        for row, argv, reason in rows:
            with self.subTest(row=row):
                self.write_snapshot([command_row(argv + ["{out}"])])
                code, lines = self.run_slot()
                self.assertEqual((code, lines[-1]), (2, "slot-run: refused: " + reason), lines)


class ChildEnvTests(SlotRunCase):
    def test_child_path_keeps_only_absolute_entries_outside_the_repository_and_the_cwd(self):
        outside = str(self.tmp / "outbin")
        cwd = self.tmp / "elsewhere"
        cwd.mkdir()
        link = make_link(self.tmp / "link", self.repo)
        rows = [
            ("an empty entry", ""),
            ("a dot entry", "."),
            ("a relative entry", "bin"),
            ("a relative entry that leaves the cwd", os.path.join("..", "outbin")),
            ("the repository root", str(self.repo)),
            ("a folder inside the repository", str(self.repo / "bin")),
            ("the working directory", str(cwd)),
            ("a folder inside the working directory", str(cwd / "bin")),
            ("a link into the repository", str(link)),
        ]
        names = ["PATH", "Path"] if NT else ["PATH"]  # Windows reads the name in any case
        for row, entry in rows:
            for name in names:
                with self.subTest(row=row, name=name):
                    env = slot_run.child_env({name: os.pathsep.join([entry, outside, entry]), "OTHER": "1"},
                                             self.repo, str(cwd))
                    self.assertEqual({key: value for key, value in env.items() if key.upper() == "PATH"},
                                     {name: outside})
                    self.assertEqual(env["OTHER"], "1")

    def test_a_path_with_every_entry_dropped_is_removed(self):
        """An empty PATH would make a POSIX exec search the working directory; with none, it takes the default."""
        env = slot_run.child_env({"PATH": os.pathsep.join(["", ".", str(self.repo)]), "OTHER": "1"},
                                 self.repo, str(self.repo))
        self.assertEqual([key for key in env if key.upper() == "PATH"], [])
        self.assertEqual(env["OTHER"], "1")

    def test_child_pythonpath_keeps_only_absolute_entries_outside_the_repository_and_the_cwd(self):
        """I2: Python reads an empty or `.` PYTHONPATH entry as the cwd, which is the repository root."""
        outside = str(self.tmp / "outlib")
        rows = [
            ("a dot entry", ".", outside),
            ("a trailing separator (an empty entry)", "", outside),
            ("a relative entry", "lib", outside),
            ("an absolute entry inside the repository", str(self.repo / "lib"), outside),
            ("only dropped entries", ".", None),
        ]
        names = ["PYTHONPATH", "PythonPath"] if NT else ["PYTHONPATH"]  # Windows reads the name in any case
        for row, entry, kept in rows:
            for name in names:
                with self.subTest(row=row, name=name):
                    given = os.pathsep.join([kept, entry] if kept else [entry, ""])
                    env = slot_run.child_env({name: given, "OTHER": "1"}, self.repo, str(self.repo))
                    self.assertEqual({key: value for key, value in env.items() if key.upper() == "PYTHONPATH"},
                                     {name: kept} if kept else {})
                    self.assertEqual(env["OTHER"], "1")

    def test_child_python_user_base_and_pycache_prefix_never_point_into_the_repository_or_the_cwd(self):
        """M7: Python runs `usercustomize` from PYTHONUSERBASE and loads cached bytecode from PYTHONPYCACHEPREFIX."""
        cwd = self.tmp / "elsewhere"
        cwd.mkdir()
        outside = str(self.tmp / "outside")
        rows = [
            ("a relative value", "ub", None),
            ("inside the repository", str(self.repo / "ub"), None),
            ("inside the working directory", str(cwd / "ub"), None),
            ("control: outside both", outside, outside),
        ]
        for variable in ("PYTHONUSERBASE", "PYTHONPYCACHEPREFIX"):
            names = [variable, variable.lower()] if NT else [variable]  # Windows reads the name in any case
            for row, given, kept in rows:
                for name in names:
                    with self.subTest(variable=variable, row=row, name=name):
                        env = slot_run.child_env({name: given, "OTHER": "1"}, self.repo, str(cwd))
                        self.assertEqual({key: value for key, value in env.items() if key.upper() == variable},
                                         {name: kept} if kept else {})
                        self.assertEqual(env["OTHER"], "1")

    def test_strip_env_applies_to_the_child(self):
        env = slot_run.child_env({"CLAUDE_X": "1", "git_dir": "1", "RIGGER_MODULE_DIRS": "1", "OTHER": "1"},
                                 self.repo, str(self.repo))
        self.assertEqual(sorted(key for key in env if key.upper() not in (SETTING.upper(), SAFE_PATH)), ["OTHER"])

    def test_python_child_leaves_its_working_directory_off_sys_path(self):
        # An empty value turns the setting off; Windows reads the name in any case.
        rows = [{}, {SAFE_PATH: ""}] + ([{SAFE_PATH.lower(): "", "PythonSafePath": "0"}] if NT else [])
        for given in rows:
            with self.subTest(given=given):
                env = slot_run.child_env(dict(given, OTHER="1"), self.repo, str(self.repo))
                self.assertEqual({key: value for key, value in env.items() if key.upper() == SAFE_PATH},
                                 {SAFE_PATH: "1"})

    @unittest.skipUnless(NT, "the setting is read by the Windows program lookup only")
    def test_windows_child_never_searches_its_working_directory(self):
        for given in ({}, {SETTING.upper(): ""}, {SETTING.lower(): "0", SETTING: "x"}):
            with self.subTest(given=given):
                env = slot_run.child_env(dict(given, OTHER="1"), self.repo, str(self.repo))
                self.assertEqual({key: value for key, value in env.items() if key.upper() == SETTING.upper()},
                                 {SETTING: "1"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
