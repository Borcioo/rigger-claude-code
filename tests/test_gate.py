"""Tests of scripts/gate.py with stand-in legs, and of the planted line against the real sweep."""

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import gate  # noqa: E402


def python(code):
    return [sys.executable, "-I", "-c", code]


PASS = python("print('leg output')")
FAIL = python("import sys; print('leg broke'); sys.exit(3)")
SLEEP = python("import time; time.sleep(60)")
# The stand-in tree id of the tests that do not run git; the real ones build a throwaway repository.
FAKE_TREE = "a" * 40


def meet(mine, other):
    """A leg that passes only while the other leg runs too."""
    return python(
        "import pathlib, sys, time\n"
        "pathlib.Path(%r).write_text('x')\n"
        "end = time.monotonic() + 20\n"
        "while not pathlib.Path(%r).exists():\n"
        "    if time.monotonic() > end: sys.exit(1)\n"
        "    time.sleep(0.05)\n" % (mine, other)
    )


class RunGateTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="gate-"))
        self.addCleanup(shutil.rmtree, self.out, True)
        # These tests need no git; the tree id has its own tests, on throwaway repositories, below.
        patcher = mock.patch.object(gate, "tree_id", return_value=FAKE_TREE)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_gate(self, stages):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            passed = gate.run_gate(stages, ROOT, self.out)
        return passed, buffer.getvalue(), (self.out / gate.STATUS).read_text(encoding="utf-8").splitlines()

    def test_every_leg_passing_passes(self):
        passed, output, status = self.run_gate([[gate.Leg("a", PASS), gate.Leg("b", PASS)]])
        self.assertTrue(passed)
        self.assertRegex(status[-1], r"^# gate passed, \d+\.\d s, tree " + FAKE_TREE + "$")
        self.assertEqual(output.splitlines(), status)
        self.assertIn("leg output", (self.out / "a.log").read_text(encoding="utf-8"))

    def test_a_load_only_count_in_a_leg_output_shows_in_its_pass_line(self):
        loaded = python("print('# files 9, tests 9, failed 0, skipped 0, load-only 2')")
        passed, _, status = self.run_gate([[gate.Leg("a", loaded), gate.Leg("b", PASS)]])
        self.assertTrue(passed)
        self.assertTrue(any(re.match(r"^PASS a \d+\.\d s, load-only 2$", line) for line in status), status)
        self.assertTrue(any(re.match(r"^PASS b \d+\.\d s$", line) for line in status), status)

    def test_a_failed_leg_is_named_and_the_others_still_run(self):
        passed, _, status = self.run_gate([[gate.Leg("bad", FAIL)], [gate.Leg("later", PASS)]])
        self.assertFalse(passed)
        self.assertRegex(status[-1], r"^# gate failed: bad, \d+\.\d s$")
        self.assertIn("START later", status)
        self.assertIn("leg broke", (self.out / "bad.log").read_text(encoding="utf-8"))
        self.assertTrue(any(line.startswith("FAIL bad: exit 3, expected 0") for line in status), status)

    def test_a_leg_that_must_fail_passes_on_its_exit_code(self):
        self.assertTrue(self.run_gate([[gate.Leg("control", FAIL, expected=3)]])[0])

    def test_a_control_that_passed_fails_the_gate(self):
        self.assertFalse(self.run_gate([[gate.Leg("control", PASS, expected=3)]])[0])

    def test_the_marker_must_be_in_the_output(self):
        self.assertTrue(self.run_gate([[gate.Leg("a", PASS, marker="leg output")]])[0])
        passed, _, status = self.run_gate([[gate.Leg("a", PASS, marker="other text")]])
        self.assertFalse(passed)
        self.assertTrue(any("other text" in line for line in status), status)

    def counted(self, code, **fields):
        leg = gate.Leg("tests", python(code), count_re=gate.RUN_TESTS_COUNT, **fields)
        return self.run_gate([[leg]])

    def test_a_counted_leg_that_prints_no_count_fails_naming_the_leg(self):
        passed, _, status = self.counted("print('leg output')")
        self.assertFalse(passed)
        self.assertTrue(any(line.startswith("FAIL tests: printed no count matching ") for line in status), status)
        self.assertRegex(status[-1], r"^# gate failed: tests, ")

    def test_a_counted_leg_that_counts_zero_fails(self):
        passed, _, status = self.counted("print('# files 3, tests 0, failed 0, skipped 3')")
        self.assertFalse(passed)
        self.assertTrue(any(line.startswith("FAIL tests: count 0 from ") for line in status), status)

    def test_the_last_count_line_decides(self):
        self.assertFalse(self.counted("print('# files 1, tests 5, failed 0, skipped 0'); print('# files 1, tests 0, failed 0, skipped 0')")[0])
        passed, _, status = self.counted("print('# files 1, tests 0, failed 0, skipped 0'); print('# files 1, tests 5, failed 0, skipped 0')")
        self.assertTrue(passed, status)

    def test_a_crlf_log_is_counted_by_a_line_end_anchored_regex(self):
        crlf = python("import sys; sys.stdout.buffer.write(b'x\\r\\n# files 4, findings 0\\r\\n')")
        passed, _, status = self.run_gate([[gate.Leg("sweep", crlf, count_re=r"^# files (\d+), findings \d+$")]])
        self.assertTrue(passed, status)
        self.assertTrue(any(re.match(r"^PASS sweep \d+\.\d s, count 4$", line) for line in status), status)

    def test_skipped_files_are_counted_apart_from_the_passing_count(self):
        code = (
            "print('SKIP tests/test_a.py: 6 tests, 6 skipped [0.1 s]')\n"
            "print('  skipped test_x (T): shellcheck not on PATH')\n"
            "print('PASS tests/test_b.py: 7 tests [0.1 s]')\n"
            "print('SKIP tests/c.tests.ps1: # skipped 1 PowerShell test file: pwsh not found, 1 skipped [0.0 s]')\n"
            "print('# files 3, tests 7, failed 0, skipped 7')"
        )
        passed, _, status = self.counted(code, skip_files=True)
        self.assertTrue(passed, status)
        self.assertTrue(any(re.match(r"^PASS tests \d+\.\d s, count 7, skipped files 2$", line) for line in status), status)

    def test_a_load_only_leg_keeps_its_count_from_the_rerun(self):
        code = (
            "print('FAIL tests/test_a.py: 1 tests, exit 1 [0.1 s]')\n"
            "print('LOAD-ONLY tests/test_a.py: failed in the shared run, passed alone [0.1 s]')\n"
            "print('# files 9, tests 9, failed 0, skipped 0, load-only 1')"
        )
        passed, _, status = self.counted(code, skip_files=True)
        self.assertTrue(passed, status)
        self.assertTrue(any(re.match(r"^PASS tests \d+\.\d s, count 9, load-only 1$", line) for line in status), status)

    def test_legs_of_a_stage_run_at_the_same_time(self):
        first, second = str(self.out / "first"), str(self.out / "second")
        legs = [gate.Leg("a", meet(first, second)), gate.Leg("b", meet(second, first))]
        self.assertTrue(self.run_gate([legs])[0])

    def test_a_stage_starts_after_the_last_one_ended(self):
        _, _, status = self.run_gate([[gate.Leg("a", PASS)], [gate.Leg("b", PASS)]])
        ended = next(index for index, line in enumerate(status) if line.startswith("PASS a"))
        self.assertLess(ended, status.index("START b"))

    def test_a_leg_over_the_limit_fails_and_is_killed(self):
        beats = self.out / "beats"
        beating = python(
            "import pathlib, time\n"
            "for beat in range(400):\n"
            "    pathlib.Path(%r).write_text(str(beat))\n"
            "    time.sleep(0.05)\n" % str(beats)
        )
        started = time.monotonic()
        with mock.patch.object(gate, "LEG_TIMEOUT", 2):
            passed, _, status = self.run_gate([[gate.Leg("slow", beating)]])
        self.assertFalse(passed)
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue(any(line.startswith("FAIL slow: timeout after 2 s") for line in status), status)
        last = beats.read_bytes() if beats.exists() else None
        time.sleep(0.5)
        self.assertEqual(beats.read_bytes() if beats.exists() else None, last, "the leg is still running")

    def test_a_leg_that_cannot_start_fails_the_gate(self):
        passed, _, status = self.run_gate([[gate.Leg("gone", [str(self.out / "no-such-program")])]])
        self.assertFalse(passed)
        self.assertTrue(any(line.startswith("FAIL gone: cannot start") for line in status), status)

    def test_an_empty_gate_fails(self):
        self.assertFalse(self.run_gate([])[0])

    def test_a_skipped_leg_prints_its_reason_runs_nothing_and_is_counted(self):
        skipped = gate.Leg("wsl", PASS, skip="wsl not on PATH")
        passed, _, status = self.run_gate([[gate.Leg("a", PASS), skipped]])
        self.assertTrue(passed, status)
        self.assertIn("SKIP wsl: wsl not on PATH", status)
        self.assertNotIn("START wsl", status)
        self.assertFalse((self.out / "wsl.log").exists())
        self.assertRegex(status[-1], r"^# gate passed, \d+\.\d s, skipped 1, tree " + FAKE_TREE + "$")

    def test_a_gate_whose_every_leg_is_skipped_fails(self):
        passed, _, status = self.run_gate([[gate.Leg("a", PASS, skip="why")]])
        self.assertFalse(passed)
        self.assertRegex(status[-1], r"^# gate failed: no leg ran, ")

    def test_notes_follow_the_tree_line(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            gate.run_gate([[gate.Leg("a", PASS)]], ROOT, self.out, notes=["denylist: none"])
        status = (self.out / gate.STATUS).read_text(encoding="utf-8").splitlines()
        self.assertEqual(status[:2], ["TREE " + FAKE_TREE, "denylist: none"])

    def test_an_exact_count_must_match(self):
        regex = r"^# files \d+, findings (\d+)$"
        two = python("import sys; print('# files 1, findings 2'); sys.exit(1)")
        passed, _, status = self.run_gate([[gate.Leg("c", two, expected=1, count_re=regex, exact=1)]])
        self.assertFalse(passed)
        self.assertTrue(any(line.startswith("FAIL c: count 2 from ") and "expected 1" in line for line in status), status)
        one = python("import sys; print('# files 1, findings 1'); sys.exit(1)")
        self.assertTrue(self.run_gate([[gate.Leg("c", one, expected=1, count_re=regex, exact=1)]])[0])

    def test_interrupted_gate_stops_its_legs(self):
        beats = self.out / "started"
        leg = gate.Leg("slow", python("import pathlib, time; pathlib.Path(%r).write_text('x'); time.sleep(60)" % str(beats)))

        def interrupted(futures):
            deadline = time.monotonic() + 30
            while not beats.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            raise KeyboardInterrupt

        started = time.monotonic()
        with mock.patch.object(gate, "collect", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_gate([[leg]])
        self.assertLess(time.monotonic() - started, 30, "the gate waited for the leg")


class StagesTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="gate-"))
        self.addCleanup(shutil.rmtree, self.out, True)
        self.args = mock.Mock(denylist="deny.txt", distro="Ubuntu")

    def names(self, stages):
        return [[leg.name for leg in stage] for stage in stages]

    def legs(self):
        return {leg.name: leg for stage in gate.build_stages(self.args, ROOT, self.out) for leg in stage}

    def windows(self, wsl="/x/wsl.exe"):
        """Patches that make build_stages see Windows, with `wsl` at that path (None: not on PATH)."""
        stack = contextlib.ExitStack()
        stack.enter_context(mock.patch.object(gate.os, "name", "nt"))
        stack.enter_context(mock.patch.object(gate, "wsl_path", return_value="/x"))
        stack.enter_context(mock.patch.object(gate.shutil, "which", lambda name: wsl if name == "wsl" else None))
        return stack

    def test_the_suites_never_run_at_the_same_time(self):
        with self.windows():
            self.assertEqual(
                self.names(gate.build_stages(self.args, ROOT, self.out)),
                [["tests", "control", "sweep", "sweep-control"], ["wsl"]],
            )

    def test_no_linux_leg_off_windows(self):
        with mock.patch.object(gate.os, "name", "posix"):
            self.assertEqual(len(gate.build_stages(self.args, ROOT, self.out)), 1)

    def test_no_wsl_on_windows_skips_the_linux_leg(self):
        with self.windows(wsl=None):
            stages = gate.build_stages(self.args, ROOT, self.out)
        self.assertEqual(self.names(stages)[-1], ["wsl"])
        self.assertEqual(stages[-1][0].skip, "wsl not on PATH")

    def test_wsl_on_windows_runs_the_linux_leg(self):
        with self.windows():
            leg = gate.build_stages(self.args, ROOT, self.out)[-1][0]
        self.assertIsNone(leg.skip)
        self.assertEqual(leg.command[:3], ["wsl", "-d", "Ubuntu"])

    def test_the_legs_use_this_interpreter(self):
        for name in ("tests", "control", "sweep", "sweep-control"):
            self.assertEqual(self.legs()[name].command[0], sys.executable, name)

    def test_the_controls_ask_for_their_control(self):
        self.assertIn("--control", self.legs()["control"].command)
        self.assertEqual(self.legs()["sweep-control"].expected, 1)
        self.assertEqual(self.legs()["control"].marker, "# control: caught")

    def test_no_claude_runs_the_suite_with_no_claude(self):
        legs = {leg.name: leg for stage in gate.build_stages(self.args, ROOT, self.out, claude=False) for leg in stage}
        self.assertIn("--no-claude", legs["tests"].command)
        self.assertNotIn("--no-claude", legs["control"].command)
        self.assertNotIn("--no-claude", self.legs()["tests"].command)

    def test_every_leg_has_a_count(self):
        with self.windows():
            legs = {leg.name: leg for stage in gate.build_stages(self.args, ROOT, self.out) for leg in stage}
        self.assertEqual(sorted(name for name, leg in legs.items() if leg.count_re is None), [])
        self.assertEqual(sorted(name for name, leg in legs.items() if leg.skip_files), ["tests", "wsl"])
        self.assertEqual(legs["wsl"].count_re, gate.RUN_TESTS_COUNT)
        self.assertEqual(legs["tests"].count_re, gate.RUN_TESTS_COUNT)

    def tool(self, *command, cwd=None):
        done = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", *map(str, command)],
            cwd=str(cwd or ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            timeout=120,
        )
        return done.stdout

    def test_the_tests_count_reads_the_real_runner(self):
        root = self.out / "root"
        (root / "scripts").mkdir(parents=True)
        (root / "tests").mkdir()
        shutil.copy(ROOT / "scripts" / "run_tests.py", root / "scripts" / "run_tests.py")
        (root / "scripts" / "run_tests.min").write_text("1\n", encoding="utf-8")
        (root / "tests" / "test_one.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n    def test_b(self): pass\nunittest.main()\n",
            encoding="utf-8",
        )
        output = self.tool(root / "scripts" / "run_tests.py", cwd=root)
        self.assertEqual(gate.last_count(self.legs()["tests"].count_re, output), 2, output)

    def test_the_control_count_reads_the_real_control(self):
        output = self.tool(ROOT / "scripts" / "run_tests.py", "--control")
        self.assertEqual(gate.last_count(self.legs()["control"].count_re, output), 1, output)

    def test_the_sweep_counts_read_the_real_sweep(self):
        clean = self.out / "clean.md"
        clean.write_text("nothing here\n", encoding="utf-8")
        output = self.tool(ROOT / "scripts" / "sweep.py", "--no-denylist", clean)
        self.assertEqual(gate.last_count(self.legs()["sweep"].count_re, output), 1, output)
        output = self.tool(ROOT / "scripts" / "sweep.py", "--no-denylist", clean, gate.plant(self.out))
        self.assertEqual(gate.last_count(self.legs()["sweep"].count_re, output), 2, output)
        self.assertEqual(gate.last_count(self.legs()["sweep-control"].count_re, output), 1, output)

    def test_the_denylist_reaches_both_sweeps(self):
        for name in ("sweep", "sweep-control"):
            self.assertIn("deny.txt", self.legs()[name].command)

    def test_wsl_path(self):
        # Built here because the sweep flags a drive path.
        self.assertEqual(gate.wsl_path("F" + ":" + r"\Worktrees\tree"), "/m" + "nt/f/Worktrees/tree")

    def test_the_planted_line_is_caught_by_the_real_sweep(self):
        planted = gate.plant(self.out)
        done = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(ROOT / "scripts" / "sweep.py"), "--no-denylist", str(planted)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(planted.name, done.stdout)

    def test_no_release_leg_without_release(self):
        self.assertNotIn("denylist-control", self.legs())


def never(*args, **kwargs):
    raise AssertionError("the gate ran")


class PublicGateTests(unittest.TestCase):
    """The gate with no private inputs, and `--release`. Every call of main patches what runs legs."""

    def setUp(self):
        self.out = Path(tempfile.mkdtemp(prefix="gate-"))
        self.addCleanup(shutil.rmtree, self.out, True)

    def main(self, argv):
        """(exit code, stdout, stderr, the stages run_gate got, its notes); nothing runs."""
        seen = {}

        def fake_run_gate(stages, root, out, notes=()):
            seen["stages"], seen["notes"] = stages, list(notes)
            return True

        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(gate, "run_gate", fake_run_gate):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    code = gate.main(argv + ["--out", str(self.out)])
                except SystemExit as exit:
                    code = exit.code
        return code, stdout.getvalue(), stderr.getvalue(), seen.get("stages"), seen.get("notes")

    def refused(self, argv):
        """main must exit 2 before building or running anything; returns its stderr."""
        with mock.patch.object(gate, "build_stages", never):
            code, _, stderr, stages, _ = self.main(argv)
        self.assertEqual(code, 2, stderr)
        self.assertIsNone(stages)
        return stderr

    def legs(self, stages):
        return {leg.name: leg for stage in stages for leg in stage}

    def denylist(self, *terms):
        path = self.out / "deny.txt"
        path.write_text("# a comment\n" + "".join(term + "\n" for term in terms), encoding="utf-8")
        return str(path)

    def which(self, *found):
        """shutil.which as seen by the gate: only the names in `found` are on PATH."""
        return mock.patch.object(gate.shutil, "which", lambda name: "/x/" + name if name in found else None)

    def test_no_inputs_builds_the_gate(self):
        with self.which("claude", "wsl"):
            code, _, _, stages, _ = self.main([])
        self.assertEqual(code, 0)
        self.assertEqual([leg.name for leg in stages[0]], ["tests", "control", "sweep", "sweep-control"])
        self.assertTrue(all(leg.skip is None for leg in stages[0]))

    def test_the_line_loss_flags_are_gone(self):
        for flag in ("--source", "--manifest"):
            with self.subTest(flag):
                self.assertIn("unrecognized arguments", self.refused([flag, "x"]))

    def test_no_claude_on_path_runs_the_suite_with_no_claude_and_says_so(self):
        with self.which("wsl"):
            _, _, _, stages, notes = self.main([])
        self.assertIn("claude: not on PATH, the suite runs with --no-claude", notes)
        self.assertIn("--no-claude", self.legs(stages)["tests"].command)

    def test_claude_on_path_runs_the_whole_suite(self):
        with self.which("claude", "wsl"):
            _, _, _, stages, notes = self.main([])
        self.assertFalse(any(note.startswith("claude:") for note in notes), notes)
        self.assertNotIn("--no-claude", self.legs(stages)["tests"].command)

    def test_no_denylist_prints_none(self):
        code, _, _, stages, notes = self.main([])
        self.assertEqual(code, 0)
        self.assertIn("denylist: none", notes)
        legs = self.legs(stages)
        for name in ("sweep", "sweep-control"):
            self.assertIn("--no-denylist", legs[name].command, name)
            self.assertNotIn("--denylist", legs[name].command, name)

    def test_a_denylist_prints_no_none_note(self):
        _, _, _, stages, notes = self.main(["--denylist", self.denylist("someterm")])
        self.assertNotIn("denylist: none", notes)
        self.assertIn("--denylist", self.legs(stages)["sweep"].command)

    def test_release_requires_denylist(self):
        self.assertIn("--release requires --denylist", self.refused(["--release"]))

    def test_release_fails_on_empty_denylist(self):
        self.assertIn("denylist has no terms", self.refused(["--release", "--denylist", self.denylist()]))

    def test_release_fails_on_a_missing_denylist(self):
        self.assertIn("denylist not found", self.refused(["--release", "--denylist", str(self.out / "gone.txt")]))

    def test_release_plants_denylist_term(self):
        # Built at run time: no literal term in this file.
        term = "".join(("pla", "nted", "qx", str(os.getpid())))
        deny = self.denylist(term)
        code, _, _, stages, _ = self.main(["--release", "--denylist", deny])
        self.assertEqual(code, 0)
        leg = self.legs(stages)["denylist-control"]
        self.assertEqual((leg.expected, leg.exact), (1, 1))
        self.assertIn(deny, leg.command)
        planted = Path(leg.command[-1])
        self.assertIn(term, planted.read_text(encoding="utf-8"))
        self.assertEqual(leg.marker, planted.name)
        # The leg passes against the real sweep.
        log = self.out / "denylist-control.log"
        with open(log, "wb") as handle:
            done = subprocess.run(leg.command, cwd=str(ROOT), stdout=handle, stderr=subprocess.STDOUT, timeout=120)
        self.assertIsNone(gate.judge(leg, done.returncode, 0.0, log), log.read_text(encoding="utf-8", errors="replace"))

    def test_release_plants_a_term_the_sweep_finds_once(self):
        tail = "".join(("qz", str(os.getpid())))
        # Planting the first term would also match the second: two findings.
        deny = self.denylist("ab" + tail, "b" + tail)
        _, _, _, stages, _ = self.main(["--release", "--denylist", deny])
        planted = Path(self.legs(stages)["denylist-control"].command[-1]).read_text(encoding="utf-8")
        self.assertIn("b" + tail, planted)
        self.assertNotIn("ab" + tail, planted)

    def test_release_refuses_a_denylist_with_no_plantable_term(self):
        self.assertIn("no term to plant", self.refused(["--release", "--denylist", self.denylist(r"\d{9}x")]))

    def test_the_release_note_and_the_term_stay_out_of_the_output(self):
        term = "".join(("sec", "retq", str(os.getpid())))
        code, stdout, stderr, _, notes = self.main(["--release", "--denylist", self.denylist(term)])
        self.assertEqual(code, 0)
        self.assertNotIn(term, stdout + stderr + "".join(notes))


def forget(function, path, error):
    """shutil.rmtree handler: git object files are read-only on Windows."""
    os.chmod(path, 0o700)
    function(path)


def git(repo, *args):
    done = subprocess.run(
        ["git", *map(str, args)],
        cwd=str(repo),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        check=True,
    )
    return done.stdout.replace("\r\n", "\n").strip()


def init_repo(path):
    path.mkdir()
    git(path, "init", "-q")
    for key, value in (("user.name", "t"), ("user.email", "t"), ("core.autocrlf", "false"), ("commit.gpgsign", "false")):
        git(path, "config", key, value)
    (path / ".gitignore").write_text("ignored/\n__pycache__/\n", encoding="utf-8", newline="\n")
    (path / "a.txt").write_text("a\n", encoding="utf-8", newline="\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "base")


class TreeRepoCase(unittest.TestCase):
    """A throwaway repository with one commit; the working tree is clean."""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="gate-"))
        self.addCleanup(shutil.rmtree, self.base, False, forget)
        self.repo = self.base / "repo"
        init_repo(self.repo)
        self.idx = self.base / "idx"
        self.idx.mkdir()

    def head_tree(self):
        return git(self.repo, "rev-parse", "HEAD^{tree}")

    def tree(self):
        return gate.tree_id(self.repo, self.idx)

    def write(self, name, text="x\n"):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


class TreeIdTests(TreeRepoCase):
    def test_a_clean_tree_has_the_id_of_head(self):
        self.assertEqual(self.tree(), self.head_tree())

    def test_the_same_content_committed_has_the_same_id(self):
        self.write("b.txt")
        (self.repo / "a.txt").unlink()
        before = self.tree()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "next")
        self.assertEqual(before, self.head_tree())

    def test_an_untracked_file_changes_the_id(self):
        before = self.tree()
        self.write("new.txt")
        self.assertNotEqual(self.tree(), before)

    def test_an_ignored_file_does_not_change_the_id(self):
        before = self.tree()
        self.write("ignored/x.txt")
        self.write("__pycache__/y.pyc")
        self.assertEqual(self.tree(), before)

    def test_a_tracked_file_that_is_ignored_stays_in_the_id(self):
        self.write("ignored/forced.txt")
        git(self.repo, "add", "-f", "ignored/forced.txt")
        git(self.repo, "commit", "-q", "-m", "forced")
        self.write("ignored/forced.txt", "edited\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "edited")
        self.assertEqual(self.tree(), self.head_tree())

    def test_the_real_index_is_untouched(self):
        self.write("new.txt")
        (self.repo / "a.txt").write_text("changed\n", encoding="utf-8", newline="\n")
        status = git(self.repo, "status", "--porcelain")
        index = (self.repo / ".git" / "index").read_bytes()
        self.tree()
        self.assertEqual(git(self.repo, "status", "--porcelain"), status)
        self.assertIn("?? new.txt", status)
        self.assertEqual((self.repo / ".git" / "index").read_bytes(), index)

    def test_a_repository_without_a_commit_fails_closed(self):
        empty = self.base / "empty"
        empty.mkdir()
        git(empty, "init", "-q")
        with self.assertRaisesRegex(gate.TreeError, "^cannot compute tree id: "):
            gate.tree_id(empty, self.idx)

    def test_a_folder_that_is_not_a_repository_fails_closed(self):
        plain = self.base / "plain"
        plain.mkdir()
        with self.assertRaisesRegex(gate.TreeError, "^cannot compute tree id: "):
            gate.tree_id(plain, self.idx)

    def test_a_missing_git_fails_closed(self):
        with mock.patch.object(gate.subprocess, "run", side_effect=FileNotFoundError("git")):
            with self.assertRaisesRegex(gate.TreeError, "^cannot compute tree id: "):
                self.tree()

    def test_a_git_that_times_out_fails_closed(self):
        with mock.patch.object(gate.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 1)):
            with self.assertRaisesRegex(gate.TreeError, "^cannot compute tree id: git read-tree: "):
                self.tree()

    def test_a_git_that_prints_no_tree_id_fails_closed(self):
        done = subprocess.CompletedProcess([], 0, stdout="not an id\n", stderr="")
        with mock.patch.object(gate.subprocess, "run", return_value=done):
            with self.assertRaisesRegex(gate.TreeError, "^cannot compute tree id: "):
                self.tree()

    def test_every_git_step_must_succeed(self):
        real = subprocess.run
        for step in ("read-tree", "add", "write-tree"):

            def broken(command, *args, _step=step, **kwargs):
                if command[1] == _step:
                    return subprocess.CompletedProcess(command, 128, stdout="", stderr="fatal: boom")
                return real(command, *args, **kwargs)

            with self.subTest(step), mock.patch.object(gate.subprocess, "run", broken):
                with self.assertRaisesRegex(gate.TreeError, "^cannot compute tree id: .*boom"):
                    self.tree()


class GateTreeTests(TreeRepoCase):
    def run_in(self, root, stages, out=None):
        out = out or self.base / "out"
        out.mkdir(exist_ok=True)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            passed = gate.run_gate(stages, root, out)
        return passed, buffer.getvalue(), (out / gate.STATUS).read_text(encoding="utf-8").splitlines()

    def test_the_status_names_the_tree_before_the_first_leg_and_after_the_last(self):
        passed, output, status = self.run_in(self.repo, [[gate.Leg("a", PASS)], [gate.Leg("b", PASS)]])
        tree = self.head_tree()
        self.assertTrue(passed, status)
        self.assertEqual(status[0], "TREE " + tree)
        self.assertEqual(status[1], "START a")
        self.assertRegex(status[-1], r"^# gate passed, \d+\.\d s, tree " + tree + "$")
        self.assertEqual(output.splitlines(), status)

    def test_a_change_during_the_run_fails_a_gate_whose_legs_all_passed(self):
        writer = python("import pathlib; pathlib.Path('late.txt').write_text('x')")
        passed, _, status = self.run_in(self.repo, [[gate.Leg("a", writer)]])
        before = self.head_tree()
        after = self.tree()
        self.assertNotEqual(before, after)
        self.assertFalse(passed)
        self.assertIn("PASS a", " ".join(status))
        self.assertIn("tree changed during the gate: %s -> %s" % (before, after), status)
        self.assertRegex(status[-1], r"^# gate failed: tree, \d+\.\d s$")
        self.assertFalse(any(line.startswith("# gate passed") for line in status), status)

    def test_an_ignored_file_written_by_a_leg_does_not_fail_the_gate(self):
        writer = python("import pathlib; p = pathlib.Path('__pycache__'); p.mkdir(); (p / 'x.pyc').write_text('x')")
        passed, _, status = self.run_in(self.repo, [[gate.Leg("a", writer)]])
        self.assertTrue(passed, status)
        self.assertTrue((self.repo / "__pycache__" / "x.pyc").exists())

    def test_a_git_failure_before_the_first_leg_fails_the_gate_and_runs_no_leg(self):
        plain = self.base / "plain"
        plain.mkdir()
        passed, _, status = self.run_in(plain, [[gate.Leg("a", PASS)]])
        self.assertFalse(passed)
        self.assertRegex(status[0], r"^cannot compute tree id: ")
        self.assertFalse(any(line.startswith("START") for line in status), status)
        self.assertRegex(status[-1], r"^# gate failed: tree, \d+\.\d s$")

    def test_a_git_failure_after_the_last_leg_fails_the_gate(self):
        ids = [FAKE_TREE, gate.TreeError("cannot compute tree id: boom")]
        with mock.patch.object(gate, "tree_id", side_effect=ids):
            passed, _, status = self.run_in(self.repo, [[gate.Leg("a", PASS)]])
        self.assertFalse(passed)
        self.assertIn("PASS a", " ".join(status))
        self.assertIn("cannot compute tree id: boom", status)
        self.assertRegex(status[-1], r"^# gate failed: tree, ")

    def test_an_out_folder_inside_the_repository_is_not_part_of_the_tree(self):
        passed, _, status = self.run_in(self.repo, [[gate.Leg("a", PASS)]], out=self.repo / "gate-out")
        self.assertTrue(passed, status)
        self.assertEqual(status[0], "TREE " + self.head_tree())


class VerifyTests(TreeRepoCase):
    def setUp(self):
        super().setUp()
        self.status = self.base / "status.txt"

    def verify(self, text=None, root=None):
        if text is not None:
            self.status.write_bytes(text.encode("utf-8"))
        never = mock.Mock(side_effect=AssertionError("--verify ran the gate"))
        buffer = io.StringIO()
        with mock.patch.object(gate, "run_gate", never), mock.patch.object(gate, "build_stages", never):
            with contextlib.redirect_stdout(buffer):
                code = gate.main(["--verify", str(self.status)], root=root or self.repo)
        return code, buffer.getvalue()

    def passed(self, tree=None):
        return "TREE x\nSTART a\nPASS a 0.1 s\n# gate passed, 0.1 s, tree %s\n" % (tree or self.head_tree())

    def test_a_pass_line_of_the_current_tree_matches(self):
        tree = self.head_tree()
        for name, text in (
            ("plain", self.passed()),
            ("blank lines after", self.passed() + "\n  \n"),
            ("crlf", self.passed().replace("\n", "\r\n")),
        ):
            with self.subTest(name):
                self.assertEqual(self.verify(text), (0, "gate result matches: tree %s\n" % tree))

    def test_another_tree_is_named_with_both_ids(self):
        other = "b" * 40
        self.assertEqual(
            self.verify(self.passed(other)),
            (1, "gate ran on tree %s, current tree is %s\n" % (other, self.head_tree())),
        )

    def test_no_pass_line_is_reported(self):
        for name, text in (
            ("failed gate", "TREE x\nSTART a\n# gate failed: a, 1.0 s\n"),
            ("empty file", ""),
            ("pass line followed by other text", self.passed() + "START late\n"),
            ("pass line not last", self.passed() + "# gate failed: a, 1.0 s\n"),
        ):
            with self.subTest(name):
                self.assertEqual(self.verify(text), (1, "no pass line in %s\n" % self.status))

    def test_a_pass_line_without_a_tree_id_is_reported(self):
        for name, text in (
            ("old format", "# gate passed, 0.1 s\n"),
            ("not an id", "# gate passed, 0.1 s, tree zzz\n"),
            ("empty id", "# gate passed, 0.1 s, tree \n"),
        ):
            with self.subTest(name):
                self.assertEqual(self.verify(text), (1, "pass line has no tree id\n"))

    def test_an_unreadable_file_is_reported(self):
        self.assertEqual(self.verify(), (1, "cannot read %s\n" % self.status))
        self.status.mkdir()
        self.assertEqual(self.verify(), (1, "cannot read %s\n" % self.status))
        self.status.rmdir()
        self.status.write_bytes(b"\xff\xfe\x00 not utf-8\n")
        self.assertEqual(self.verify(), (1, "cannot read %s\n" % self.status))

    def test_a_git_failure_fails_closed_and_removes_its_temp_folder(self):
        plain = self.base / "plain"
        plain.mkdir()
        made = []
        real = tempfile.mkdtemp

        def recording(*args, **kwargs):
            made.append(real(*args, **kwargs))
            return made[-1]

        with mock.patch.object(gate.tempfile, "mkdtemp", recording):
            code, output = self.verify(self.passed(), root=plain)
        self.assertEqual(code, 1)
        self.assertRegex(output, r"^cannot compute tree id: [^\n]+\n$")
        self.assertEqual(len(made), 1)
        self.assertFalse(Path(made[0]).exists())

    def test_the_temp_folder_is_removed_after_a_match(self):
        made = []
        real = tempfile.mkdtemp

        def recording(*args, **kwargs):
            made.append(real(*args, **kwargs))
            return made[-1]

        with mock.patch.object(gate.tempfile, "mkdtemp", recording):
            self.assertEqual(self.verify(self.passed())[0], 0)
        self.assertEqual(len(made), 1)
        self.assertFalse(Path(made[0]).exists())

    def test_a_gate_run_is_verified_until_the_tree_changes(self):
        out = self.base / "out"
        out.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(gate.run_gate([[gate.Leg("a", PASS)]], self.repo, out))
        self.status = out / gate.STATUS
        tree = self.head_tree()
        self.assertEqual(self.verify(), (0, "gate result matches: tree %s\n" % tree))
        self.write("new.txt")
        self.assertEqual(self.verify(), (1, "gate ran on tree %s, current tree is %s\n" % (tree, self.tree())))

    def test_a_failed_gate_is_never_verified(self):
        out = self.base / "out"
        out.mkdir()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertFalse(gate.run_gate([[gate.Leg("a", FAIL)]], self.repo, out))
        self.status = out / gate.STATUS
        self.assertEqual(self.verify(), (1, "no pass line in %s\n" % self.status))

    def test_the_script_verifies_without_the_gate_arguments(self):
        missing = self.base / "missing.txt"
        done = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(ROOT / "scripts" / "gate.py"), "--verify", str(missing)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
        )
        self.assertEqual((done.returncode, done.stdout.replace("\r\n", "\n")), (1, "cannot read %s\n" % missing))


class MainTests(unittest.TestCase):
    def test_exit_code_follows_the_gate(self):
        out = tempfile.mkdtemp(prefix="gate-")
        self.addCleanup(shutil.rmtree, out, True)
        argv = ["--denylist", "d", "--out", out]
        for passed, code in ((True, 0), (False, 1)):
            with self.subTest(passed), mock.patch.object(gate, "run_gate", return_value=passed):
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(gate.main(argv), code)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="backslashreplace")
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main(verbosity=2)
