"""rigger-panel: runs the plugin's own tests (`claude plugin test`), checks the source is portable and
that it can only read GitHub: no gh write command and no MCP tool that writes."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SOURCES = sorted(path for suffix in ("*.ts", "*.tsx") for path in (PLUGIN / "hooks").glob(suffix))
MODULE = [path for path in SOURCES if not path.name.endswith((".test.ts", ".test.tsx"))]
# `N pass` / `N fail` are the last totals `claude plugin test` prints.
PASS_RE = re.compile(r"^\s*(\d+) pass\s*$", re.M)
FAIL_RE = re.compile(r"^\s*(\d+) fail\s*$", re.M)
# A drive letter or a user's home folder written into the source ties the plugin to one machine.
MACHINE_PATH_RE = re.compile(r"""['"`]([A-Za-z]:[\\/]|/Users/|/home/)""")

# What separates two words of a command, whether it is written as one string or as an argv array.
SEP = r"""['"`\s,]+"""
# The panel only reads. Each pattern is one way to write: a gh issue subcommand that changes an
# issue, any gh pr command, gh api with a method or a field, a git command other than
# `git remote get-url`, a program other than gh and git, an MCP tool named like a GitHub write, or an
# MCP call to any server but the session app's.
Q = r"""['"`]"""
WRITE_RES = {
    "gh issue write": re.compile(
        r"(?i)\bissue" + SEP + r"(create|edit|close|reopen|comment|delete|lock|unlock|transfer|pin|unpin|develop)\b"
    ),
    "gh pr": re.compile(r"(?i)\bgh" + SEP + r"pr\b"),
    "gh api write": re.compile(
        r"(?i)\bgh" + SEP + r"api\b[^\n]*?" + r"""['"`\s,=](-X|--method|-f|-F|--field|--raw-field|--input)\b"""
    ),
    "git write": re.compile(Q + r"git" + Q + r"\s*,\s*" + Q + r"(?!remote" + Q + r"\s*,\s*" + Q + r"get-url" + Q + ")"),
    "other program": re.compile(r"process\.run\(\s*\[\s*(?!" + Q + r"(gh|git)" + Q + ")"),
    "MCP write tool": re.compile(
        r"(?i)\b(add|edit|create|update|delete|close|merge|assign|lock|push|fork|submit|dismiss)[-_ ]?([a-z]+[-_]){0,3}"
        r"(comment|issue|pull[-_ ]?request|label|milestone|review|branch|file|release|repository)s?\b"
    ),
    "MCP server": re.compile(r"""\bmcp\.call\(\s*(?!SESSIONS\b|['"`]ccd_session['"`])"""),
}

PLANTED = {
    "gh issue write": [
        "await $.process.run(['gh', 'issue', 'create', '--title', 'x'])",
        "await $.process.run(['gh', 'issue', 'comment', '13', '--body', 'x'])",
        "await $.process.run(['gh', \"issue\", 'close', '13'])",
        "const line = `gh issue edit 13 --add-label bug`",
        "await $.process.run(['gh', 'issue', 'delete', '13', '--yes'])",
        "await $.process.run(['gh', 'issue', 'reopen', '13'])",
    ],
    "gh pr": ["await $.process.run(['gh', 'pr', 'list'])", "'gh pr merge 1'"],
    "gh api write": [
        "await $.process.run(['gh', 'api', 'repos/x/y/issues', '-X', 'POST'])",
        "await $.process.run(['gh', 'api', 'repos/x/y/issues/1', '--method', 'PATCH'])",
        "await $.process.run(['gh', 'api', 'repos/x/y/issues', '-f', 'title=x'])",
        "await $.process.run(['gh', 'api', 'repos/x/y/issues', '-F', 'title=x'])",
        "await $.process.run(['gh', 'api', 'repos/x/y/issues', '--field', 'title=x'])",
        "await $.process.run(['gh', 'api', 'repos/x/y/issues', '--raw-field', 'title=x'])",
        "await $.process.run(['gh', 'api', 'graphql', '--input', 'q.json'])",
        "'gh api repos/x/y/issues --method=POST'",
    ],
    "MCP write tool": [
        "await $.mcp.call(SESSIONS, 'create_issue', {})",
        "await $.mcp.call(SESSIONS, 'add_issue_comment', {})",
        "await $.mcp.call(SESSIONS, 'update_issue', {})",
        "await $.mcp.call(SESSIONS, 'merge_pull_request', {})",
        "await $.mcp.call(SESSIONS, 'CreatePullRequest', {})",
        "await $.mcp.call(SESSIONS, 'create_or_update_file', {})",
        "await $.mcp.call(SESSIONS, 'push_files', {})",
        "await $.mcp.call(SESSIONS, 'create_branch', {})",
    ],
    "git write": [
        "await $.process.run(['git', 'push', 'origin', 'main'])",
        "await $.process.run(['git', 'commit', '-m', 'x'])",
        "await $.process.run([\"git\", \"tag\", \"v1\"])",
        "await $.process.run(['git', 'remote', 'set-url', 'origin', 'x'])",
        "await $.process.run(['git', 'remote', 'add', 'x', 'y'])",
    ],
    "other program": [
        "await $.process.run(['sh', '-c', 'curl -X POST x'])",
        "await $.process.run(\n    ['pwsh', '-Command', 'x'],\n  )",
        "await $.process.run([program, 'x'])",
    ],
    "MCP server": ["await $.mcp.call('github', 'list_issues', {})", "await $.mcp.call(GITHUB, 'get_issue', {})"],
}

# Reads that must stay allowed: the scan is no use if it refuses the panel's own commands.
ALLOWED = [
    "await $.process.run(['gh', 'issue', 'list', '--repo', target, '--json', 'number,title,state,url'])",
    "`gh issue view ${number} --repo ${repo} --comments`",
    "await $.process.run(['gh', 'auth', 'status', '--hostname', 'github.com'])",
    "await $.process.run(['gh', 'api', 'repos/x/y/issues'])",
    "await $.process.run(['git', 'remote', 'get-url', 'origin'], { timeoutMs: RUN_MS })",
    "const list = await $.process.run(\n    ['gh', 'issue', 'list'],\n  )",
    "await update($, plan, steps => closePlan(steps, running))",
    "$.mcp.call(SESSIONS, 'start_session', startArgs(repo, number, title))",
    "$.mcp.call(SESSIONS, 'spawn_task', spawnArgs(repo, number, title))",
    "The issue stays read-only: no comments, labels, state changes or new issues.",
    "await update($, issues, last => ({ ...last, starts }))",
]


def write_hits(text):
    return sorted(name for name, pattern in WRITE_RES.items() if pattern.search(text))


class PanelTests(unittest.TestCase):
    def test_source_files_found(self):
        names = [path.name for path in SOURCES]
        for name in ("register.tsx", "config-dir.ts", "issue-flow.ts", "issues.ts"):
            self.assertIn(name, names)

    def test_no_github_write_in_the_module(self):
        for path in MODULE:
            with self.subTest(file=path.name):
                self.assertEqual(write_hits(path.read_text(encoding="utf-8")), [], path.name)

    def test_the_module_does_read_issues_with_gh(self):
        # The control for the scan above: the read it allows is really there.
        text = (PLUGIN / "hooks" / "issues.ts").read_text(encoding="utf-8")
        self.assertRegex(text, r"'gh', 'issue', 'list'")
        self.assertRegex(text, r"'git', 'remote', 'get-url', 'origin'")
        self.assertIn("gh issue view", text)

    def test_write_scan_catches_every_planted_write(self):
        for name, lines in PLANTED.items():
            for line in lines:
                with self.subTest(planted=line):
                    self.assertIn(name, write_hits(line))

    def test_write_scan_allows_the_reads(self):
        for line in ALLOWED:
            with self.subTest(allowed=line):
                self.assertEqual(write_hits(line), [])

    def test_no_machine_path_in_the_module(self):
        for path in MODULE:
            with self.subTest(file=path.name):
                self.assertIsNone(MACHINE_PATH_RE.search(path.read_text(encoding="utf-8")), path.name)

    def test_claude_plugin_test(self):
        if os.environ.get("HARNESS_NO_CLAUDE") == "1":
            self.skipTest("HARNESS_NO_CLAUDE=1 (run_tests.py --no-claude): claude plugin test not run")
        claude = shutil.which("claude")
        self.assertIsNotNone(claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        config = tempfile.mkdtemp(prefix="harness-panel-")
        self.addCleanup(shutil.rmtree, config, True)
        proc = subprocess.run(
            [claude, "plugin", "test", str(PLUGIN)],
            env=dict(os.environ, CLAUDE_CONFIG_DIR=config),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=100,
        )
        output = proc.stdout.decode("utf-8", "replace").replace("\r\n", "\n")
        self.assertEqual(proc.returncode, 0, output)
        passed = PASS_RE.findall(output)
        self.assertTrue(passed and int(passed[-1]) >= 130, output)
        self.assertEqual(FAIL_RE.findall(output)[-1:], ["0"], output)


if __name__ == "__main__":
    unittest.main()
