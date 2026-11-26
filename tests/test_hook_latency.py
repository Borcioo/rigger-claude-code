"""Tests of scripts/hook_latency.py: a fixture plugins folder with known fast and slow handlers, and the real
tree's handler list against CASES."""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import hook_latency as hl  # noqa: E402

RUNS = 3
SLOW_SECONDS = 0.2
LINE = re.compile(r"# hook (\S+) (\S+) (\S+) (\S+) median (\d+) p95 (\d+) floor (\d+)( async)?")
NOT_STARTED = re.compile(r"# hook (\S+) (\S+) (\S+) non-match not started")
SLOW_PY = "import sys, time\nsys.stdin.buffer.read()\ntime.sleep(%r)\nprint('SLOW')\n" % SLOW_SECONDS


def python_command(script):
    """A handler command that runs `script` under this Python, quoted for the launcher shell."""
    return '"%s" "${CLAUDE_PLUGIN_ROOT}/%s"' % (Path(sys.executable).as_posix(), script)


def write_plugin(base, name, hooks):
    root = base / name
    (root / "hooks").mkdir(parents=True)
    (root / "slow.py").write_bytes(SLOW_PY.encode("utf-8"))
    if hooks is not None:
        (root / "hooks" / "hooks.json").write_bytes(json.dumps({"hooks": hooks}).encode("utf-8"))
    return root


def slow_case(ctx, handler):
    variants = [hl.Variant("match", ctx.stdin(handler, {"tool_input": {"command": "x"}}), hl.expect(b"SLOW"))]
    if handler.event == "PreToolUse" and handler.condition is None:
        variants.append(hl.Variant("non-match", ctx.stdin(handler, {"tool_input": {"command": "y"}}),
                                   hl.expect(b"SLOW")))
    return variants


def fast_case(ctx, handler):
    return [hl.Variant("match", ctx.stdin(handler, {}), hl.silent)]


FIXTURE_CASES = {("fast", "exit"): fast_case, ("slow", "slow.py"): slow_case}


def fixture(base):
    plugins = base / "plugins"
    write_plugin(plugins, "fast", {"SessionStart": [{"hooks": [{"type": "command", "command": "exit 0"}]}]})
    write_plugin(plugins, "slow", {
        "SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": python_command("slow.py")}]}],
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": python_command("slow.py")},
            {"type": "command", "if": "Bash(*x*)", "command": python_command("slow.py") + " guarded"}]}],
    })
    return plugins


def run(plugins, cases, runs=RUNS):
    lines = []
    code = hl.run(plugins, cases, runs, lines.append)
    return code, lines


class FixtureRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.base = Path(tempfile.mkdtemp(prefix="hook-latency-test-"))
        cls.code, cls.lines = run(fixture(cls.base), FIXTURE_CASES)
        cls.out = "\n".join(cls.lines)
        cls.measured = [LINE.fullmatch(line) for line in cls.lines]
        cls.measured = [match for match in cls.measured if match]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(str(cls.base), ignore_errors=True)

    def find(self, plugin, event, case):
        found = [m for m in self.measured if (m.group(1), m.group(2), m.group(4)) == (plugin, event, case)]
        self.assertEqual(len(found), 1, self.out)
        return found[0]

    def test_slow_handler_reads_slower_than_fast(self):
        self.assertEqual(self.code, 0, self.out)
        fast = int(self.find("fast", "SessionStart:*", "match").group(5))
        slow = self.find("slow", "SessionStart:startup", "match")
        self.assertGreaterEqual(int(slow.group(5)), SLOW_SECONDS * 1000, self.out)
        self.assertLess(fast, int(slow.group(5)) - SLOW_SECONDS * 1000 * 0.75, self.out)
        self.assertLess(int(slow.group(7)), int(slow.group(5)) - SLOW_SECONDS * 1000 * 0.75, self.out)
        self.assertIn("# hooks measured 4 of 4", self.lines, self.out)

    def test_if_handler_non_match_is_not_started(self):
        rows = [NOT_STARTED.fullmatch(line) for line in self.lines]
        rows = [m.groups() for m in rows if m]
        self.assertEqual(rows, [("slow", "PreToolUse:Bash", "slow.py:guarded@Bash(*x*)")], self.out)
        guarded = [m for m in self.measured if m.group(3) == "slow.py:guarded@Bash(*x*)"]
        self.assertEqual([m.group(4) for m in guarded], ["match"], self.out)

    def test_candidate_line_only_on_the_per_command_path(self):
        candidates = [line for line in self.lines if line.startswith("# candidate for a Rust port:")]
        self.assertEqual(len(candidates), 2, self.out)
        for line in candidates:
            self.assertIn(" slow PreToolUse:Bash ", line)
        self.assertNotIn("SessionStart", "\n".join(candidates))


class FailureTests(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="hook-latency-test-"))
        self.addCleanup(shutil.rmtree, str(self.base), True)

    def assert_failed(self, code, lines, reason):
        out = "\n".join(lines)
        self.assertEqual(code, 1, out)
        failed = [line for line in lines if line.startswith("# hook latency failed: ")]
        self.assertEqual(len(failed), 1, out)
        self.assertIn(reason, failed[0])

    def test_handler_without_a_case_fails_the_run(self):
        plugins = fixture(self.base)
        no_non_match = dict(FIXTURE_CASES)
        no_non_match[("slow", "slow.py")] = lambda ctx, h: slow_case(ctx, h)[:1]
        rows = [({("fast", "exit"): fast_case}, "no case for slow slow.py"),
                (no_non_match, "no non-match case for slow PreToolUse:Bash slow.py")]
        for cases, reason in rows:
            with self.subTest(reason=reason):
                self.assert_failed(*run(plugins, cases, runs=1), reason=reason)

    def test_no_hooks_file_fails_the_run(self):
        plugins = self.base / "plugins"
        write_plugin(plugins, "empty", None)
        self.assert_failed(*run(plugins, FIXTURE_CASES, runs=1), reason="no hook handlers")

    def test_a_case_that_misses_its_proof_fails_the_run(self):
        plugins = fixture(self.base)
        wrong = dict(FIXTURE_CASES)
        wrong[("fast", "exit")] = lambda ctx, h: [hl.Variant("match", ctx.stdin(h, {}), hl.expect(b"never printed"))]
        self.assert_failed(*run(plugins, wrong, runs=1), reason="fast SessionStart:* exit match")


class LauncherTests(unittest.TestCase):
    def test_handlers_start_through_the_observed_launcher(self):
        tmp = tempfile.mkdtemp(prefix="hook-latency-test-")
        self.addCleanup(shutil.rmtree, tmp, True)
        done = hl.spawn('printf "%s|%s" "$0" "${BASH_VERSION-}"', b"", dict(os.environ), tmp)
        zero, version = done.stdout.decode("utf-8").split("|")
        if os.name == "nt":   # <Git>\bin\bash.exe -c, which runs usr\bin\bash.exe (Task 17 (c))
            self.assertEqual(zero, "/usr/bin/bash")
            self.assertRegex(version, r"^[5-9]\.")
        else:                 # /bin/sh -c (Task 17 (c) on Linux; assumed on macOS)
            self.assertEqual(zero, "/bin/sh")

    def test_without_git_bash_the_run_says_why(self):
        tmp = Path(tempfile.mkdtemp(prefix="hook-latency-test-"))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        empty, lone = tmp / "empty", tmp / "Git" / "cmd"
        empty.mkdir()
        lone.mkdir(parents=True)
        (lone / "git.exe").write_bytes(b"")
        os.chmod(str(lone / "git.exe"), 0o755)
        rows = [(empty, "no Git Bash: git not found"), (lone, "no Git Bash: no bin\\bash.exe with usr\\bin\\bash.exe")]
        saved = os.environ.get("PATH")
        try:
            for folder, reason in rows:
                with self.subTest(reason=reason):
                    os.environ["PATH"] = str(folder)
                    with self.assertRaises(RuntimeError) as caught:
                        hl.git_bash()
                    self.assertIn(reason, str(caught.exception))
        finally:
            os.environ["PATH"] = saved


class NearestRankTests(unittest.TestCase):
    def test_nearest_rank(self):
        rows = [(list(range(1, 21)), 95, 19), (list(range(1, 21)), 50, 10), ([3, 1, 2], 50, 2),
                ([3, 1, 2], 95, 3), ([7], 95, 7), (list(range(1, 11)), 95, 10)]
        for values, pct, want in rows:
            with self.subTest(values=values, pct=pct):
                self.assertEqual(hl.nearest_rank(values, pct), want)


class RealTreeTests(unittest.TestCase):
    def test_every_real_handler_has_a_case(self):
        handlers = hl.handlers(ROOT / "plugins")
        want = sum(len(group.get("hooks", []))
                   for path in sorted((ROOT / "plugins").glob("*/hooks/hooks.json"))
                   for groups in json.loads(path.read_bytes().decode("utf-8")).get("hooks", {}).values()
                   for group in groups)
        self.assertEqual(len(handlers), want)
        self.assertEqual(want, 41)
        keys = {(h.plugin, h.script) for h in handlers}
        self.assertEqual(sorted(keys - set(hl.CASES)), [], "handlers without a case")
        self.assertEqual(sorted(set(hl.CASES) - keys), [], "cases without a handler")

    def test_shellwise_cases_take_their_paths(self):
        base = Path(tempfile.mkdtemp(prefix="hook-latency-test-"))
        self.addCleanup(shutil.rmtree, str(base), True)
        shutil.copytree(str(ROOT / "plugins" / "rigger-shellwise"), str(base / "plugins" / "rigger-shellwise"),
                        ignore=shutil.ignore_patterns("__pycache__"))
        code, lines = run(base / "plugins", hl.CASES, runs=1)
        out = "\n".join(lines)
        self.assertEqual(code, 0, out)
        rows = sorted((m.group(2), m.group(3), m.group(4)) for m in (LINE.fullmatch(line) for line in lines) if m)
        self.assertEqual(rows, [("PreToolUse:Bash|PowerShell", "shellwise.sh", "match"),
                                ("PreToolUse:Bash|PowerShell", "shellwise.sh", "non-match"),
                                ("SessionStart:startup|clear|compact|resume", "shellwise_install.py:--session-start",
                                 "match")],
                         out)
        self.assertIn("# hooks measured 2 of 2", lines, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
