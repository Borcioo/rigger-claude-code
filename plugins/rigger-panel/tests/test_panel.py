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
HOOKS = PLUGIN / "hooks"
SOURCES = sorted(path for suffix in ("*.ts", "*.tsx") for path in HOOKS.rglob(suffix))
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
# `git remote get-url` and `git rev-parse --path-format=absolute --git-common-dir`, a program other
# than gh and git, a program whose argv is not written out, an MCP tool named like a GitHub write, or
# an MCP call to any server but the session app's (module tabs excepted: MANIFEST_CALLS).
Q = r"""['"`]"""
WRITE_RES = {
    "gh issue write": re.compile(
        r"(?i)\bissue" + SEP + r"(create|edit|close|reopen|comment|delete|lock|unlock|transfer|pin|unpin|develop)\b"
    ),
    "gh pr": re.compile(r"(?i)\bgh" + SEP + r"pr\b"),
    "gh api write": re.compile(
        r"(?i)\bgh" + SEP + r"api\b[^\n]*?" + r"""['"`\s,=](-X|--method|-f|-F|--field|--raw-field|--input)\b"""
    ),
    "git write": re.compile(
        Q + r"git" + Q + r"\s*,\s*" + Q + r"(?!remote" + Q + r"\s*,\s*" + Q + r"get-url" + Q
        + r"|rev-parse" + Q + r"\s*,\s*" + Q + r"--path-format=absolute" + Q + r"\s*,\s*" + Q + r"--git-common-dir" + Q
        + r"\s*\])"
    ),
    "other program": re.compile(r"process\.run\(\s*\[\s*(?!" + Q + r"(gh|git)" + Q + ")"),
    "MCP write tool": re.compile(
        r"(?i)\b(add|edit|create|update|delete|close|merge|assign|lock|push|fork|submit|dismiss)[-_ ]?([a-z]+[-_]){0,3}"
        r"(comment|issue|pull[-_ ]?request|label|milestone|review|branch|file|release|repository)s?\b"
    ),
    "MCP server": re.compile(r"""\bmcp\.call\(\s*(?!SESSIONS\b|['"`]ccd_session['"`])"""),
    "variable program": re.compile(r"process\.run\((?!\s*\[)"),
}

# A module tab's source comes from that module's manifest (README "Module tabs"): the one MCP call and
# the one program start that run it are allowed in this file, at exactly these spellings, once each.
MANIFEST_CALLS = {
    "module-tabs.ts": (
        "$.mcp.call(source.mcp.server, source.mcp.tool, source.mcp.args)",
        "$.process.run(argv, { ...(cwd ? { cwd } : {}), timeoutMs: CALL_MS })",
    ),
}


# The inbox (README "Inbox") asks the desktop app who this session is, and hands a session link to the
# system opener: these calls are allowed in these files, at exactly these spellings, once each. The
# server, the tools and the opener argv they use are pinned by INBOX_PINS, so a changed constant or a new
# tool is still a hit.
INBOX_CALLS = {
    "inbox/writer.ts": ("$.mcp.call(APP, tool, args)",),
    "inbox/reader.ts": (
        "$.process.run(['uname', '-s'], { timeoutMs: 5000 })",
        "$.process.run(openCommand(await sysnameOf($), entry.link), { timeoutMs: 10_000 })",
    ),
}
APP_TOOLS = {"get_session", "list_sessions"}
OPENER_RETURNS = {"return ['open', link]", "return ['cmd', '/c', 'start', '', link]", "return ['xdg-open', link]"}
FILE_CALLS = {**MANIFEST_CALLS, **INBOX_CALLS}


def inbox_pin_hits(name, text):
    """The rules behind INBOX_CALLS: writer.ts calls only ccd_session_mgmt; callApp, defined there and not
    exported, is called (in any file) only with one of APP_TOOLS as a literal name; reader.ts's
    openCommand returns only the OPENER_RETURNS argv."""
    hits = set()
    text = text.replace("\r\n", "\n")
    if name == "inbox/writer.ts" and "const APP = 'ccd_session_mgmt'\n" not in text:
        hits.add("app server")
    calls = re.findall(r"\bcallApp\(", text)
    tools = re.findall(r"\bcallApp\(\s*\$\s*,\s*'([A-Za-z0-9_]+)'", text)
    defined = len(re.findall(r"^async function callApp\(", text, re.M)) if name == "inbox/writer.ts" else 0
    if len(tools) != len(calls) - defined or not set(tools) <= APP_TOOLS:
        hits.add("app tool")
    if name == "inbox/reader.ts":
        body = re.search(r"export function openCommand\([^)]*\)[^{]*\{(.*?)\n\}", text, re.S)
        returns = re.findall(r"return\b[^\n]*", body.group(1)) if body else []
        if not returns or any(line.strip() not in OPENER_RETURNS for line in returns):
            hits.add("opener argv")
    return hits


def module_hits(name, text):
    """write_hits for one module file (its path under hooks/), with that file's allowed calls taken out."""
    pins = inbox_pin_hits(name, text)
    for spelling in FILE_CALLS.get(name, ()):
        text = text.replace(spelling, "", 1)
    return sorted(set(write_hits(text)) | pins)


def rel(path):
    return path.relative_to(HOOKS).as_posix()

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
        "await $.process.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir', '--output', 'x'])",
        "await $.process.run(['git', 'rev-parse', 'HEAD'])",
    ],
    "other program": [
        "await $.process.run(['sh', '-c', 'curl -X POST x'])",
        "await $.process.run(\n    ['pwsh', '-Command', 'x'],\n  )",
        "await $.process.run([program, 'x'])",
    ],
    "MCP server": ["await $.mcp.call('github', 'list_issues', {})", "await $.mcp.call(GITHUB, 'get_issue', {})"],
    "variable program": ["await $.process.run(argv, { cwd })", "$.process.run(\n    command,\n  )"],
}

# Reads that must stay allowed: the scan is no use if it refuses the panel's own commands.
ALLOWED = [
    "await $.process.run(['gh', 'issue', 'list', '--repo', target, '--json', 'number,title,state,url'])",
    "`gh issue view ${number} --repo ${repo} --comments`",
    "await $.process.run(['gh', 'auth', 'status', '--hostname', 'github.com'])",
    "await $.process.run(['gh', 'api', 'repos/x/y/issues'])",
    "await $.process.run(['git', 'remote', 'get-url', 'origin'], { timeoutMs: RUN_MS })",
    "await $.process.run(['git', 'rev-parse', '--path-format=absolute', '--git-common-dir'], {",
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
        names = [rel(path) for path in SOURCES]
        for name in (
            "register.tsx",
            "config-dir.ts",
            "issue-flow.ts",
            "issues.ts",
            "inbox/entries.ts",
            "inbox/writer.ts",
            "inbox/reader.ts",
            "inbox/view.tsx",
        ):
            self.assertIn(name, names)

    def test_no_github_write_in_the_module(self):
        for path in MODULE:
            with self.subTest(file=rel(path)):
                self.assertEqual(module_hits(rel(path), path.read_text(encoding="utf-8")), [], rel(path))

    def test_manifest_calls_appear_once_each_where_allowed(self):
        # The control for the allowance: each allowed spelling is really there, once.
        for name, spellings in FILE_CALLS.items():
            text = (HOOKS / name).read_text(encoding="utf-8")
            for spelling in spellings:
                with self.subTest(file=name, spelling=spelling):
                    self.assertEqual(text.count(spelling), 1)

    def test_manifest_calls_are_allowed_nowhere_else(self):
        mcp, run = MANIFEST_CALLS["module-tabs.ts"]
        planted = [
            ("register.tsx", mcp, "MCP server"),
            ("register.tsx", run, "variable program"),
            ("module-tabs.ts", mcp + "\n" + mcp, "MCP server"),
            ("module-tabs.ts", "$.mcp.call(other.server, source.mcp.tool, source.mcp.args)", "MCP server"),
            ("module-tabs.ts", "$.process.run(command, { timeoutMs: CALL_MS })", "variable program"),
        ]
        for name, text, rule in planted:
            with self.subTest(file=name, planted=text):
                self.assertIn(rule, module_hits(name, text))

    def test_inbox_calls_are_allowed_only_as_written(self):
        writer = (HOOKS / "inbox" / "writer.ts").read_text(encoding="utf-8")
        reader = (HOOKS / "inbox" / "reader.ts").read_text(encoding="utf-8")
        mcp = INBOX_CALLS["inbox/writer.ts"][0]
        uname, opener = INBOX_CALLS["inbox/reader.ts"]
        # The real files pass (the allowed controls), and each planted line beside them is still a hit.
        self.assertEqual(module_hits("inbox/writer.ts", writer), [])
        self.assertEqual(module_hits("inbox/reader.ts", reader), [])
        planted = [
            ("inbox/writer.ts", writer + "\nawait callApp($, 'send_message', { text: 'x' })", "app tool"),
            ("inbox/writer.ts", writer + "\nawait callApp($, tool, {})", "app tool"),
            ("inbox/writer.ts", writer.replace("const APP = 'ccd_session_mgmt'", "const APP = 'github'"), "app server"),
            ("inbox/writer.ts", writer + "\n$.mcp.call('ccd_session_mgmt', 'send_message', {})", "MCP server"),
            ("inbox/writer.ts", writer + "\n" + mcp, "MCP server"),
            ("inbox/reader.ts", reader + "\nawait $.process.run(['curl', '-X', 'POST', 'x'])", "other program"),
            ("inbox/reader.ts", reader + "\n" + opener, "variable program"),
            ("inbox/reader.ts", reader + "\n" + uname, "other program"),
            ("inbox/reader.ts", reader.replace("return ['xdg-open', link]", "return ['sh', '-c', link]"), "opener argv"),
            ("inbox/view.tsx", "await $.process.run(['open', link])", "other program"),
            ("inbox/view.tsx", mcp, "MCP server"),
            ("inbox/view.tsx", "await callApp($, 'send_message', {})", "app tool"),
            ("inbox/writer.ts", writer.replace("async function callApp(", "export async function callApp("), "app tool"),
            ("register.tsx", opener, "variable program"),
            ("register.tsx", uname, "other program"),
        ]
        for name, text, rule in planted:
            with self.subTest(file=name, rule=rule, planted=text[-60:]):
                self.assertIn(rule, module_hits(name, text))

    def test_the_module_does_read_issues_with_gh(self):
        # The control for the scan above: the read it allows is really there.
        text = (PLUGIN / "hooks" / "issues.ts").read_text(encoding="utf-8")
        self.assertRegex(text, r"'gh', 'issue', 'list'")
        self.assertRegex(text, r"'git', 'remote', 'get-url', 'origin'")
        self.assertIn("gh issue view", text)
        tabs = (PLUGIN / "hooks" / "tabs.ts").read_text(encoding="utf-8")
        self.assertIn("['git', 'rev-parse', '--path-format=absolute', '--git-common-dir']", tabs)

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
            with self.subTest(file=rel(path)):
                self.assertIsNone(MACHINE_PATH_RE.search(path.read_text(encoding="utf-8")), rel(path))

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
        self.assertTrue(passed and int(passed[-1]) >= 339, output)
        self.assertEqual(FAIL_RE.findall(output)[-1:], ["0"], output)


if __name__ == "__main__":
    unittest.main()
