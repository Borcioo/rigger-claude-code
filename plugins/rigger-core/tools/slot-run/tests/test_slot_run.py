"""slot_run: a command slot run with no shell, from the repository root, inside its bounds, its output checked.

python -I -X utf8 test_slot_run.py
"""
import ctypes
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
TOOL_DIR = os.path.dirname(HERE)
TOOLS = os.path.dirname(TOOL_DIR)
SCRIPT = os.path.join(TOOL_DIR, "slot_run.py")
FAKE = os.path.join(HERE, "fake_reviewer.py")
sys.path.insert(0, TOOL_DIR)
sys.path.insert(1, os.path.join(TOOLS, "assemble"))

import slot_run  # noqa: E402
import slots  # noqa: E402
import snapshot  # noqa: E402

NT = os.name == "nt"
REAL_RUN = subprocess.run
REAL_POPEN = subprocess.Popen
SESSION = "session-1"
SETTING = "NoDefaultCurrentDirectoryInExePath"
SAFE_PATH = "PYTHONSAFEPATH"
NOT_FOUND = "slot-run: refused: reviewer-cli not found on PATH (entries inside the repository are not searched)"
BARE_OR_ABSOLUTE = "slot-run: refused: the program must be a bare name or an absolute path"
# The scope of the fixture: a.py edited, b.py renamed to d.py (a delete and an add), c.py deleted.
SCOPE_TEXT = "review\ta.py\nskip: deleted\tb.py\nskip: deleted\tc.py\nreview\td.py\n"
_TEMPLATE = []  # [repository, base id, head id] of the fixture, built by the first test that needs it


def remove_tree(path):
    def onerror(func, target, _exc):
        os.chmod(target, 0o700)
        func(target)

    shutil.rmtree(str(path), onerror=onerror)


def same_path(left, right):
    return os.path.normcase(os.path.realpath(str(left))) == os.path.normcase(os.path.realpath(str(right)))


def inside(path, root):
    path = os.path.normcase(os.path.realpath(str(path)))
    root = os.path.normcase(os.path.realpath(str(root)))
    return path == root or path.startswith(root + os.sep)


def pid_alive(pid):
    if NT:
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        kernel.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie is gone; without /proc (macOS) the signal answer stands
        with open("/proc/%d/stat" % pid, "rb") as handle:
            return handle.read().split(b") ", 1)[1][:1] != b"Z"
    except OSError:
        return True


def command_row(argv, source="personal", model=None, kind="command", filled=True):
    return slots.Row("reviewer", kind, argv, model, source, (), None, filled)


class SlotRunCase(unittest.TestCase):
    """A temp config dir, a two-commit repository and a git environment free of the host's config."""

    def setUp(self):
        self.tmp = Path(os.path.abspath(tempfile.mkdtemp(prefix="slot-run-test-")))
        self.addCleanup(remove_tree, self.tmp)
        self.config = self.tmp / "config"
        empty_config = self.tmp / "gitconfig"
        empty_config.write_bytes(b"")
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
        env.update(
            CLAUDE_CONFIG_DIR=str(self.config),
            GIT_CONFIG_NOSYSTEM="1",
            GIT_CONFIG_GLOBAL=str(empty_config),
            GIT_CEILING_DIRECTORIES=str(self.tmp),
            GIT_AUTHOR_NAME="test",
            GIT_AUTHOR_EMAIL="test",
            GIT_COMMITTER_NAME="test",
            GIT_COMMITTER_EMAIL="test",
        )
        for name in [name for name in env if name.upper() == SETTING.upper()]:  # Windows keeps names upper case
            del env[name]
        patcher = mock.patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        template, self.base, self.head = self.template()  # before tempdir moves into this test's folder
        work = self.tmp / "work"
        work.mkdir()
        temp_patch = mock.patch.object(tempfile, "tempdir", str(work))
        temp_patch.start()
        self.addCleanup(temp_patch.stop)
        self.record = str(self.tmp / "record.json")
        self.repo = self.tmp / "repo"
        shutil.copytree(template, str(self.repo))  # each test owns a copy; git runs once per file, not per test

    def template(self):
        """The fixture repository, built once per test file: a.py edited, b.py renamed to d.py, c.py deleted."""
        if not _TEMPLATE:
            folder = Path(os.path.abspath(tempfile.mkdtemp(prefix="slot-run-template-")))
            unittest.addModuleCleanup(remove_tree, folder)
            repo = folder / "repo"
            repo.mkdir()
            git = self.git_in(repo, folder)
            git("init", "-q")
            git("config", "core.autocrlf", "false")
            for name, data in (("a.py", b"x = 1\n"), ("b.py", b"b\n"), ("c.py", b"c\n")):
                (repo / name).write_bytes(data)
            git("add", "-A")
            git("commit", "-q", "-m", "one")
            base = git("rev-parse", "HEAD")
            (repo / "a.py").write_bytes(b"x = 2\n")
            git("mv", "b.py", "d.py")
            git("rm", "-q", "c.py")
            git("add", "-A")
            git("commit", "-q", "-m", "two")
            _TEMPLATE.extend([str(repo), base, git("rev-parse", "HEAD")])
        return _TEMPLATE

    def git_in(self, repo, ceiling):
        def git(*args):
            env = dict(os.environ, GIT_CEILING_DIRECTORIES=str(ceiling))
            proc = REAL_RUN(["git"] + list(args), cwd=str(repo), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            return proc.stdout.decode("utf-8").strip()
        return git

    def command(self, mode, *extra, program=None):
        return [program or sys.executable, FAKE, mode, "--record", self.record, "--out", "{out}",
                "--scope", "{scope}", "--base", "{base}", "--head", "{head}"] + list(extra)

    def write_snapshot(self, rows, trusted=(), session=SESSION):
        data = {"format": snapshot.SNAPSHOT_FORMAT, "parts": [], "modules": [], "config": {}, "sections": [],
                "rows": [], "slots": slots.as_snapshot(rows, frozenset(trusted)), "notices": []}
        self.assertIsNotNone(snapshot.write_snapshot(self.config, session, data))

    def run_slot(self, args=None, cwd=None, slot="reviewer"):
        """slot_run.main from `cwd` (the repository root by default), as a session's shell tool starts it."""
        if args is None:
            args = ["--session", SESSION, "--base", self.base, "--head", self.head]
        out = io.BytesIO()
        old = os.getcwd()
        os.chdir(str(cwd or self.repo))
        try:
            code = slot_run.main([slot] + list(args), stdout=out)
        finally:
            os.chdir(old)
        return code, out.getvalue().decode("utf-8").splitlines()

    def recorded(self):
        with open(self.record, "rb") as handle:
            return json.loads(handle.read().decode("utf-8"))

    def output_path(self, lines):
        prefix = "slot-run: output "
        self.assertGreaterEqual(len(lines), 2, lines)
        self.assertTrue(lines[-2].startswith(prefix), lines)
        return lines[-2][len(prefix):]


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


def make_link(link, target):
    """A junction on Windows, a symlink on POSIX: `link` leads to the folder `target`."""
    if NT:
        import _winapi
        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(str(target), str(link))
    return link


NODE_REVIEWER = (
    "const r = require('child_process').spawnSync('git', ['--version']);\n"
    "require('fs').writeFileSync(process.argv[2], JSON.stringify(r.error ? {error: String(r.error.code)}\n"
    "    : {rc: r.status, stdout: String(r.stdout)}));\n")


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


class BoundTests(SlotRunCase):
    def test_timeout_kills_the_tree_and_refuses(self):
        """Review Focus 3: a command that never exits and leaves a grandchild."""
        self.write_snapshot([command_row(self.command("hang"))])
        calls = []

        def recording_popen(*args, **kwargs):
            calls.append(args[0] if args else kwargs["args"])
            return REAL_POPEN(*args, **kwargs)

        started = time.monotonic()
        with mock.patch.object(slot_run, "TIMEOUT", 2), mock.patch.object(subprocess, "Popen", recording_popen):
            code, lines = self.run_slot()
        elapsed = time.monotonic() - started
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: timed out after 2 s"), lines)
        self.assertLess(elapsed, 15)
        with open(self.record + ".child.pid", "rb") as handle:
            pid = int(handle.read())
        deadline = time.monotonic() + 5
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertFalse(pid_alive(pid), "the grandchild %d outlived the kill" % pid)
        for argv0 in [call[0] for call in calls]:  # git, the command and the tree kill: never by bare name
            self.assertTrue(os.path.isabs(argv0) and not inside(argv0, self.repo), argv0)

    def test_output_over_the_cap_refuses(self):
        """Review Focus 3 and Q4: the console is read through a pipe and kept to the cap, at two caps."""
        for mode, cap in (("flood", 10000), ("flood", 1000000), ("flood-err", 10000)):
            with self.subTest(mode=mode, cap=cap):
                self.write_snapshot([command_row(self.command(mode))])
                started = time.monotonic()
                # TIMEOUT stays far above the bound, so a missing bound fails on the reason, never hangs.
                with mock.patch.object(slot_run, "CONSOLE_CAP", cap), mock.patch.object(slot_run, "TIMEOUT", 20):
                    code, lines = self.run_slot()
                elapsed = time.monotonic() - started
                self.assertEqual((code, lines[-1]), (2, "slot-run: refused: more than %d bytes of console output"
                                                     % cap), lines)
                console = os.path.join(os.path.dirname(self.output_path(lines)), "console.txt")
                self.assertEqual(os.path.getsize(console), cap)
                self.assertLess(elapsed, 30)
                pid = self.recorded()["pid"]
                deadline = time.monotonic() + 5
                while pid_alive(pid) and time.monotonic() < deadline:
                    time.sleep(0.1)
                self.assertFalse(pid_alive(pid), "the flooding command %d outlived the refusal" % pid)

    def test_output_over_the_cap_refuses_after_a_clean_exit(self):
        self.write_snapshot([command_row(self.command("flood-exit", "--bytes", "10100"))])
        with mock.patch.object(slot_run, "CONSOLE_CAP", 10000):
            code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: more than 10000 bytes of console output"), lines)

    def grandchild_pid(self):
        with open(self.record + ".child.pid", "rb") as handle:
            return int(handle.read())

    def assert_gone_after_the_run(self, pid):
        """Gone within a second of slot_run's return: the kill belongs to the run, not to a later cleanup."""
        deadline = time.monotonic() + 1
        while pid_alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(pid_alive(pid), "process %d outlived the run" % pid)

    def test_a_descendant_left_holding_the_console_is_stopped_and_refuses(self):
        """The command exits 0 with a valid file, but a process it started sleeps 30 s holding the console:
        refused after the exit grace, long before the deadline, with that process gone."""
        self.assertEqual(slot_run.EXIT_GRACE, 5)  # the default the 10 s bound below is written for
        self.write_snapshot([command_row(self.command("orphan"))])
        started = time.monotonic()
        with mock.patch.object(slot_run, "TIMEOUT", 60), mock.patch.object(slot_run, "EXIT_GRACE", 1):
            code, lines = self.run_slot()
        elapsed = time.monotonic() - started
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: child process left running"), lines)
        self.assertLess(elapsed, 10)
        self.assert_gone_after_the_run(self.grandchild_pid())
        self.assert_gone_after_the_run(self.recorded()["pid"])

    @unittest.skipUnless(NT, "the job object is Windows only")
    def test_a_command_outside_a_job_never_runs(self):
        self.write_snapshot([command_row(self.command("ok"))])
        with mock.patch.object(slot_run, "_enter_job", return_value=False), mock.patch.object(slot_run, "TIMEOUT", 5):
            code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (2, "slot-run: refused: the command could not be placed in a job object"))
        self.assertFalse(os.path.exists(self.record), "the suspended command ran")

    @unittest.skipUnless(NT, "the job object is Windows only; on POSIX the group outlives a killed slot_run")
    def test_killing_slot_run_stops_the_command_tree(self):
        """The shell tool's own timeout kills slot_run: the job closes with it, and so does the command tree."""
        self.write_snapshot([command_row(self.command("hang"))])
        proc = REAL_POPEN([sys.executable, "-I", "-X", "utf8", SCRIPT, "reviewer", "--session", SESSION, "--base",
                           self.base, "--head", self.head], cwd=str(self.repo), stdin=subprocess.DEVNULL,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          env=dict(os.environ, TMP=tempfile.tempdir, TEMP=tempfile.tempdir))  # its work folder
        try:
            deadline = time.monotonic() + 30
            while not os.path.exists(self.record + ".child.pid") and time.monotonic() < deadline:
                time.sleep(0.05)
            pid = self.grandchild_pid()
            self.assertTrue(pid_alive(pid))
        finally:
            proc.kill()
            proc.wait(timeout=30)
        self.assert_gone_after_the_run(pid)
        self.assert_gone_after_the_run(self.recorded()["pid"])

    def test_a_descendant_that_let_go_of_the_console_is_stopped(self):
        """A process the command started, with its console on the null device, does not outlive the run."""
        self.write_snapshot([command_row(self.command("detached"))])
        code, lines = self.run_slot()
        self.assertEqual((code, lines[-1]), (0, "slot-run: ok reviewer 1 findings"), lines)
        self.assert_gone_after_the_run(self.grandchild_pid())


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


if __name__ == "__main__":
    unittest.main(verbosity=2)
