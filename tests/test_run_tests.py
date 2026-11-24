"""Tests of scripts/run_tests.py's process handling: a kill that fails and an interrupted run."""

import contextlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import run_tests  # noqa: E402

BOGUS_PID = 4000001


class FakeProc:
    """A child whose wait() plays `effects` in order (an exception is raised, a number returned).

    A wait with no timeout fails the test at once: the real one would hang the run."""

    pid = BOGUS_PID

    def __init__(self, effects):
        self.effects = list(effects)
        self.timeouts = []
        self.kills = 0

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        if timeout is None:
            raise AssertionError("wait() without a timeout: it would never return")
        effect = self.effects.pop(0)
        if isinstance(effect, BaseException):
            raise effect
        return effect

    def kill(self):
        self.kills += 1


def never_settles():
    return subprocess.TimeoutExpired("child", 1)


class RunFileKillTests(unittest.TestCase):
    @contextlib.contextmanager
    def fake_child(self, proc):
        """Run run_file against `proc`; yields the tree-kill calls (nothing real is killed)."""
        tree_kills = []

        def fake_run(command, **kwargs):
            tree_kills.append(("taskkill", command, kwargs))
            return subprocess.CompletedProcess(command, 0)

        def fake_killpg(pid, sig):
            tree_kills.append(("killpg", pid, sig))

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(run_tests.subprocess, "Popen", return_value=proc))
            stack.enter_context(mock.patch.object(run_tests.subprocess, "run", side_effect=fake_run))
            stack.enter_context(mock.patch.object(run_tests.os, "killpg", side_effect=fake_killpg, create=True))
            yield tree_kills

    def run_file(self):
        return run_tests.run_file(ROOT / "tests" / "test_never_started.py", {}, root=ROOT, timeout=5)

    def test_a_process_that_survives_the_kill_is_a_timeout_note_not_an_exception(self):
        proc = FakeProc([never_settles(), never_settles()])
        with self.fake_child(proc):
            try:
                result = self.run_file()
            except subprocess.TimeoutExpired:
                self.fail("run_file let the survivor's TimeoutExpired escape and lost the result")
        self.assertFalse(result["ok"])
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["note"], "timeout after 5 s; the process survived the kill")
        self.assertEqual(proc.timeouts, [5, run_tests.KILL_TIMEOUT])
        self.assertEqual(proc.kills, 1)

    def test_a_killed_process_is_a_plain_timeout(self):
        proc = FakeProc([never_settles(), 1])
        with self.fake_child(proc):
            result = self.run_file()
        self.assertEqual(result["note"], "timeout after 5 s")
        self.assertEqual(proc.timeouts, [5, run_tests.KILL_TIMEOUT])

    def waited_limit(self, path):
        """The first wait() bound run_file gave a child that exits at once, with no explicit timeout."""
        proc = FakeProc([0])
        with self.fake_child(proc):
            run_tests.run_file(path, {}, root=ROOT)
        return proc.timeouts[0]

    def test_a_file_named_in_timeouts_gets_its_own_limit_and_others_keep_the_default(self):
        named = ROOT / "tests" / "test_named_slow.py"
        with mock.patch.object(run_tests, "TIMEOUTS", {"tests/test_named_slow.py": 777}):
            self.assertEqual(self.waited_limit(named), 777)
            self.assertEqual(self.waited_limit(ROOT / "tests" / "test_other.py"), run_tests.TIMEOUT)

    def test_a_path_outside_the_root_keeps_the_default_limit(self):
        proc = FakeProc([0])
        with self.fake_child(proc):
            run_tests.run_file(Path(sys.executable), {}, root=ROOT / "scripts")
        self.assertEqual(proc.timeouts[0], run_tests.TIMEOUT)

    def test_every_named_limit_is_for_a_file_that_exists_and_is_above_the_default(self):
        self.assertTrue(run_tests.TIMEOUTS)
        for rel, limit in run_tests.TIMEOUTS.items():
            self.assertTrue((ROOT / rel).is_file(), rel)
            self.assertGreater(limit, run_tests.TIMEOUT, rel)

    def test_the_taskkill_step_is_bounded_and_a_hung_one_does_not_stop_the_kill(self):
        proc = FakeProc([0])
        calls = []

        def hung_taskkill(command, **kwargs):
            calls.append(kwargs.get("timeout"))
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout"))

        windows = SimpleNamespace(name="nt")
        with mock.patch.object(run_tests, "os", windows), mock.patch.object(
            run_tests.subprocess, "run", side_effect=hung_taskkill
        ):
            run_tests.kill_tree(proc)
        self.assertEqual(calls, [run_tests.KILL_TIMEOUT])
        self.assertEqual(proc.kills, 1)
        self.assertEqual(proc.timeouts, [run_tests.KILL_TIMEOUT])

    def tree_kill_names(self, tree_kills, proc):
        return [entry for entry in tree_kills if str(proc.pid) in str(entry)]

    def test_ctrl_c_kills_the_child_tree_and_propagates(self):
        proc = FakeProc([KeyboardInterrupt(), 0])
        with self.fake_child(proc) as tree_kills:
            with self.assertRaises(KeyboardInterrupt):
                self.run_file()
        self.assertEqual(len(self.tree_kill_names(tree_kills, proc)), 1, tree_kills)
        self.assertEqual(proc.kills, 1)

    def test_ctrl_c_still_propagates_when_the_child_survives_the_kill(self):
        proc = FakeProc([KeyboardInterrupt(), never_settles()])
        with self.fake_child(proc) as tree_kills:
            with self.assertRaises(KeyboardInterrupt):
                self.run_file()
        self.assertEqual(len(self.tree_kill_names(tree_kills, proc)), 1, tree_kills)
        self.assertEqual(proc.kills, 1)


class ChildEnvTests(unittest.TestCase):
    PASSED = ("RIGGER_RULES_ROOT", "RIGGER_COORDINATE_MD", "RIGGER_CLI_HOST_MD")

    def test_module_dirs_is_dropped_and_other_rigger_names_pass(self):
        """main hands run_suite the env every test file gets; run_suite and run_file are faked, so nothing runs."""
        seen = []

        def fake_suite(groups, env, root, jobs, minimum, log, stop_at_failure=False):
            seen.append(env)
            return 0

        def no_file(*args, **kwargs):
            raise AssertionError("run_file called: the faked suite must run nothing")

        parent = {"RIGGER_MODULE_DIRS": "/staged/a", "rigger_module_dirs": "/staged/b"}
        parent.update({name: "/knob/" + name for name in self.PASSED})
        with mock.patch.dict(run_tests.os.environ, parent):
            with mock.patch.object(run_tests, "run_suite", fake_suite), mock.patch.object(run_tests, "run_file", no_file):
                with contextlib.redirect_stdout(io.StringIO()):
                    code = run_tests.main(["--min-files", "1"])
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        env = seen[0]
        self.assertEqual([key for key in env if key.upper() == "RIGGER_MODULE_DIRS"], [])
        for name in self.PASSED:
            with self.subTest(name=name):
                self.assertEqual(env.get(name), "/knob/" + name)


class LoadRetryTests(unittest.TestCase):
    """A file that fails in the shared run is rerun alone; run_file is faked, so nothing is timed."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="load-retry-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.calls = []
        self.active = 0
        self.overlap = []
        self.lock = threading.Lock()

    def make_files(self, names):
        (self.root / "tests").mkdir(exist_ok=True)
        for name in names:
            (self.root / "tests" / ("test_%s.py" % name)).write_text("", encoding="utf-8")

    def fake_run_file(self, outcomes):
        """outcomes: file stem -> results by attempt (True passes); other files always pass."""

        def fake(path, env, root):
            stem = path.stem[len("test_"):]
            with self.lock:
                self.active += 1
                attempt = len([call for call in self.calls if call == stem])
                self.calls.append(stem)
                self.overlap.append((stem, attempt, self.active))
            time.sleep(0.05)
            results = outcomes.get(stem, [True])
            ok = results[min(attempt, len(results) - 1)]
            result = run_tests.evaluate(0 if ok else 1, "Ran 1 test in 0.0s\n\n%s\n" % ("OK" if ok else "FAILED (failures=1)"))
            result.update(output="output of %s attempt %d\n" % (stem, attempt), seconds=0.0)
            with self.lock:
                self.active -= 1
            return result

        return fake

    def main(self, outcomes, *argv):
        buffer = io.StringIO()
        with mock.patch.object(run_tests, "run_file", self.fake_run_file(outcomes)):
            with mock.patch.object(run_tests, "SERIAL", ()):
                with contextlib.redirect_stdout(buffer):
                    code = run_tests.main(list(argv), root=self.root)
        return code, buffer.getvalue()

    def test_a_file_that_passes_alone_counts_as_passed_and_is_named(self):
        self.make_files(["a", "b"])
        code, output = self.main({"a": [False, True]}, "--min-files", "1", "--jobs", "2")
        self.assertEqual(code, 0, output)
        self.assertIn("LOAD-ONLY tests/test_a.py: failed in the shared run, passed alone", output)
        self.assertIn("# files 2, tests 2, failed 0, skipped 0, load-only 1", output)
        self.assertEqual(self.calls.count("a"), 2)

    def test_a_load_only_file_counts_the_tests_of_its_rerun(self):
        self.make_files(["a", "b"])
        counts = {("a", 0): (1, "FAILED (failures=1)"), ("a", 1): (3, "OK")}

        def fake(path, env, root):
            stem = path.stem[len("test_"):]
            attempt = self.calls.count(stem)
            self.calls.append(stem)
            ran, outcome = counts.get((stem, attempt), (1, "OK"))
            result = run_tests.evaluate(0 if outcome == "OK" else 1, "Ran %d tests in 0.0s\n\n%s\n" % (ran, outcome))
            result.update(output="", seconds=0.0)
            return result

        buffer = io.StringIO()
        with mock.patch.object(run_tests, "run_file", fake), mock.patch.object(run_tests, "SERIAL", ()):
            with contextlib.redirect_stdout(buffer):
                code = run_tests.main(["--min-files", "1", "--jobs", "1"], root=self.root)
        output = buffer.getvalue()
        self.assertEqual(code, 0, output)
        self.assertIn("# files 2, tests 4, failed 0, skipped 0, load-only 1\n", output)

    def test_a_file_that_fails_alone_too_stays_red_with_both_outputs(self):
        self.make_files(["a", "b"])
        code, output = self.main({"a": [False]}, "--min-files", "1", "--jobs", "2")
        self.assertEqual(code, 1, output)
        self.assertNotIn("LOAD-ONLY", output)
        self.assertIn("output of a attempt 0", output)
        self.assertIn("FAIL tests/test_a.py (alone)", output)
        self.assertIn("output of a attempt 1", output)
        self.assertIn("# files 2, tests 2, failed 1, skipped 0\n", output)
        self.assertEqual(self.calls.count("a"), 2)

    def test_more_than_the_limit_failing_is_a_real_break_and_not_rerun(self):
        names = ["f%d" % index for index in range(run_tests.RERUN_LIMIT + 1)]
        self.make_files(names)
        code, output = self.main({name: [False, True] for name in names}, "--min-files", "1")
        self.assertEqual(code, 1, output)
        self.assertEqual(sorted(self.calls), sorted(names))
        self.assertNotIn("LOAD-ONLY", output)

    def test_the_limit_itself_is_still_rerun(self):
        names = ["f%d" % index for index in range(run_tests.RERUN_LIMIT)]
        self.make_files(names)
        code, output = self.main({name: [False, True] for name in names}, "--min-files", "1")
        self.assertEqual(code, 0, output)
        self.assertIn("load-only %d" % run_tests.RERUN_LIMIT, output)

    def test_reruns_run_one_at_a_time_with_no_other_file(self):
        names = ["a", "b", "c", "d"]
        self.make_files(names)
        code, output = self.main({"a": [False, True], "b": [False, True], "c": [False, True]}, "--min-files", "1", "--jobs", "4")
        self.assertEqual(code, 0, output)
        reruns = [seen for seen in self.overlap if seen[1] == 1]
        self.assertEqual(len(reruns), 3)
        self.assertEqual([active for _, _, active in reruns], [1, 1, 1])

    def test_a_fix_round_does_not_rerun(self):
        self.make_files(["a"])
        code, output = self.main({"a": [False, True]}, "--fix-round", "--jobs", "1")
        self.addCleanup(lambda: run_tests.failed_path(self.root).unlink(missing_ok=True))
        self.addCleanup(shutil.rmtree, run_tests.log_path(self.root).parent, True)
        self.assertEqual(code, 1, output)
        self.assertEqual(self.calls, ["a"])


def fixture_git(cwd, *args):
    env = dict(run_tests.os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t" + "@example.invalid",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t" + "@example.invalid")
    proc = subprocess.run(["git", "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false"] + list(args), cwd=str(cwd),
                          env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")


# Written out here, not read from run_tests.ALWAYS_RUN, so a mutant of that tuple cannot shrink the expectation too.
ALWAYS_RUN = (
    "tests/test_module_contracts.py",
    "tests/test_repo.py",
    "tests/test_plugin_refs.py",
    "plugins/rigger-rules/tests/test_plugin_rules.py",
)
PASSING_TEST ="import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        pass\n\n\nunittest.main()\n"
# The stub sweep prints the line kept next to the repository (outside it, so changing it is no change) and logs its run.
SWEEP_STUB = """import os, sys
here = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
with open(os.path.join(here, "sweep-calls.txt"), "a", encoding="utf-8") as calls:
    calls.write(" ".join(sys.argv[1:]) + "\\n")
try:
    with open(os.path.join(here, "sweep-line.txt"), encoding="utf-8") as line:
        print(line.read().strip())
except OSError:
    print("# files 3, findings 0")
"""


class ChangedModulesTests(unittest.TestCase):
    """--changed-modules on a fixture repository: modules a, b (depends on a), c, rigger-core and rigger-rules,
    plugins/zzz without a manifest, the four always-run files and a stub sweep. run_file is a recording fake."""

    MODULE_TESTS = {
        name: "plugins/%s/tests/test_x.py" % name for name in ("a", "b", "c", "rigger-core")
    }

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="changed-modules-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.root = self.tmp / "repo"
        files = {
            "README.md": "# r\n",
            "docs/a.md": "doc\n",
            "scripts/sweep.py": SWEEP_STUB,
            "plugins/zzz/x.py": "x = 1\n",
            "plugins/a/f.py": "f = 1\n",
        }
        for name, deps in (("a", None), ("b", ["a"]), ("c", None), ("rigger-core", None), ("rigger-rules", None)):
            manifest = {"name": name}
            if deps is not None:
                manifest["dependencies"] = deps
            files["plugins/%s/rigger-module.json" % name] = '{"name": "%s"}\n' % name
            files["plugins/%s/.claude-plugin/plugin.json" % name] = json.dumps(manifest) + "\n"
            if name != "rigger-rules":
                files["plugins/%s/tests/test_x.py" % name] = PASSING_TEST
        for rel in ALWAYS_RUN:
            files[rel] = PASSING_TEST
        for rel, text in files.items():
            self.write(rel, text)
        fixture_git(self.root, "init", "-q", "-b", "main")
        fixture_git(self.root, "add", "-A")
        fixture_git(self.root, "commit", "-q", "-m", "base")
        self.ran = []

    def write(self, rel, text):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")

    def fake_run_file(self, failing=()):
        def fake(path, env, root=None, timeout=None):
            rel = path.relative_to(self.root).as_posix()
            self.ran.append(rel)
            ok = rel not in failing
            result = run_tests.evaluate(0 if ok else 1, "Ran 1 test in 0.0s\n\n%s\n" % ("OK" if ok else "FAILED (failures=1)"))
            result.update(output="", seconds=0.0)
            return result

        return fake

    def main(self, *argv, failing=()):
        buffer = io.StringIO()
        with mock.patch.object(run_tests, "run_file", self.fake_run_file(failing)):
            with contextlib.redirect_stdout(buffer):
                code = run_tests.main(["--changed-modules", "--jobs", "1"] + list(argv), root=self.root)
        return code, buffer.getvalue().replace("\r\n", "\n")

    def sweep_calls(self):
        try:
            return (self.tmp / "sweep-calls.txt").read_text(encoding="utf-8").splitlines()
        except OSError:
            return []

    def expect_run(self, code, output, modules):
        self.assertEqual(code, 0, output)
        expected = set(ALWAYS_RUN) | {self.MODULE_TESTS[name] for name in modules}
        self.assertEqual(set(self.ran), expected, output)
        self.assertEqual(self.sweep_calls(), ["--no-denylist"], output)
        self.assertIn("# changed-modules: sweep files 3, findings 0\n", output)

    def test_classify_table(self):
        rows = {
            ("shared", None): [
                "scripts/x.py", "tests/test_x.py", "plugins/a/rules/r.md", "plugins/a/bin/py.sh",
                "plugins/a/hooks/core_check.sh", "plugins/rigger-core/rigger-defaults.json",
                ".github/workflows/test.yml", ".claude-plugin/marketplace.json", "pyproject.toml", "plugins/zzz/x.py",
            ],
            ("docs", None): ["docs/a.md", "README.md", "LICENSE"],
            ("module", "a"): [
                "plugins/a/tools/t/t.py", "plugins/a/hooks/hooks.json", "plugins/a/skills/s/SKILL.md",
                "plugins/a/README.md", "plugins/a/agents/x.md",
            ],
        }
        for expected, paths in rows.items():
            for path in paths:
                with self.subTest(path=path):
                    self.assertEqual(run_tests.classify(path, self.root), expected)

    def test_shared_file_exits_two_and_runs_nothing(self):
        self.write("scripts/x.py", "x = 1\n")
        code, output = self.main()
        self.assertEqual(code, 2, output)
        self.assertIn("changed-modules: shared file scripts/x.py; run the gate\n", output)
        self.assertEqual(self.ran, [])
        self.assertEqual(self.sweep_calls(), [])

    def test_module_change_runs_the_module_its_dependents_and_the_always_files(self):
        self.write("plugins/a/tools/t/t.py", "t = 1\n")
        code, output = self.main()
        self.expect_run(code, output, ["a", "b"])
        self.assertIn("# changed-modules: base main, changed 1, modules a, b\n", output)

    def test_a_module_with_an_unreadable_plugin_json_runs_with_any_changed_module(self):
        self.write("plugins/d/rigger-module.json", '{"name": "d"}\n')
        self.write("plugins/d/.claude-plugin/plugin.json", '{"dependencies": 3}\n')
        self.write("plugins/d/tests/test_x.py", PASSING_TEST)
        fixture_git(self.root, "add", "-A")
        fixture_git(self.root, "commit", "-q", "-m", "add d")
        self.write("plugins/c/tools/t/t.py", "t = 1\n")
        code, output = self.main()
        self.assertEqual(code, 0, output)
        self.assertEqual(set(self.ran), set(ALWAYS_RUN) | {"plugins/c/tests/test_x.py", "plugins/d/tests/test_x.py"}, output)

    def test_a_new_skill_folder_runs_the_rules_tests(self):
        self.write("plugins/a/skills/x/SKILL.md", "---\nname: x\n---\n")
        code, output = self.main()
        self.expect_run(code, output, ["a", "b"])
        self.assertIn("plugins/rigger-rules/tests/test_plugin_rules.py", self.ran)

    def test_docs_only_change_runs_the_always_files_and_the_sweep(self):
        self.write("docs/a.md", "changed\n")
        self.write("README.md", "# changed\n")
        code, output = self.main()
        self.expect_run(code, output, [])
        self.assertIn("# changed-modules: base main, changed 2, modules none\n", output)

    def test_deleted_module_and_untracked_shared_file_exit_two(self):
        fixture_git(self.root, "rm", "-r", "-q", "plugins/c")
        code, output = self.main()
        self.assertEqual(code, 2, output)
        self.assertIn("changed-modules: shared file plugins/c/.claude-plugin/plugin.json; run the gate\n", output)
        self.assertEqual(self.ran, [])
        fixture_git(self.root, "reset", "-q", "--hard")
        self.write("tests/new.py", "n = 1\n")
        code, output = self.main()
        self.assertEqual(code, 2, output)
        self.assertIn("changed-modules: shared file tests/new.py; run the gate\n", output)
        self.assertEqual(self.ran, [])
        self.assertEqual(self.sweep_calls(), [])

    def test_moved_file_runs_both_modules(self):
        fixture_git(self.root, "mv", "plugins/a/f.py", "plugins/c/f.py")
        code, output = self.main()
        self.expect_run(code, output, ["a", "b", "c"])
        self.assertIn("# changed-modules: base main, changed 2, modules a, b, c\n", output)

    def test_sweep_with_findings_or_zero_files_fails(self):
        self.write("plugins/c/tools/t.py", "t = 1\n")
        for line in ("# files 3, findings 2", "# files 0, findings 0"):
            with self.subTest(line=line):
                (self.tmp / "sweep-line.txt").write_text(line + "\n", encoding="utf-8")
                code, output = self.main()
                self.assertEqual(code, 1, output)
                files, findings = line[len("# files "):].split(", findings ")
                self.assertIn("# changed-modules: sweep files %s, findings %s\n" % (files, findings), output)

    def test_git_failure_exits_two(self):
        self.write("plugins/a/tools/t/t.py", "t = 1\n")
        code, output = self.main("--base", "no-such-rev")
        self.assertEqual(code, 2, output)
        self.assertRegex(output, r"(?m)^changed-modules: git failed: .+; run the gate$")
        self.assertEqual(self.ran, [])
        self.assertEqual(self.sweep_calls(), [])

    def test_a_failing_module_test_exits_one(self):
        self.write("plugins/c/tools/t.py", "t = 1\n")
        code, output = self.main(failing=("plugins/c/tests/test_x.py",))
        self.assertEqual(code, 1, output)
        self.assertIn("FAIL plugins/c/tests/test_x.py", output)

    def test_sweep_findings_are_printed_before_the_count_line(self):
        self.write("plugins/c/tools/t.py", "t = 1\n")
        finding_lines = ["plugins/c/tools/t.py:1: host example.invalid", "plugins/c/tools/t.py:2: path /abs/x"]
        (self.tmp / "sweep-line.txt").write_text("\n".join(finding_lines + ["# files 3, findings 2"]) + "\n", encoding="utf-8")
        code, output = self.main()
        self.assertEqual(code, 1, output)
        lines = output.splitlines()
        count_at = lines.index("# changed-modules: sweep files 3, findings 2")
        self.assertEqual(lines[count_at - 2:count_at], finding_lines, output)

    def test_fix_round_control_or_agent_next_to_it_is_refused(self):
        for flag in ("--fix-round", "--control", "--agent"):
            with self.subTest(flag=flag):
                errors = io.StringIO()
                with contextlib.redirect_stderr(errors), self.assertRaises(SystemExit) as raised:
                    self.main(flag)
                self.assertEqual(raised.exception.code, 2)
                self.assertIn("--changed-modules", errors.getvalue())
                self.assertIn(flag, errors.getvalue())
                self.assertNotIn("unrecognized arguments", errors.getvalue())
                self.assertEqual(self.ran, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
