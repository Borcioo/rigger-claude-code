"""Tests of scripts/run_tests.py's process handling: a kill that fails and an interrupted run."""

import contextlib
import io
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
