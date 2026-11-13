"""Compact and resume each leave exactly one copy of every rules file in what the model receives.

Real `claude -p` sessions with a temp config, against a local stand-in for the API (stub_api.py), so
no login or cost. Each rules file is its own marker: its first line, counted in the last request of
the session, which is found by a unique word in the probe's prompt.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from collections import Counter
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from stub_api import StubApi  # noqa: E402

PLUGINS = Path(__file__).resolve().parent.parent.parent
INJECTED = ("rigger-core", "rigger-rules", "rigger-lessons", "rigger-talk", "rigger-windows", "rigger-wp")
SUBAGENT_ONLY = {"lessons-subagent.md"}
ANCESTOR_SENTINEL = "ancestor-claude-md-sentinel"


def first_line(path):
    return next(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def markers():
    found = {}
    for plugin in INJECTED:
        for path in sorted((PLUGINS / plugin / "rules").glob("*.md")):
            if path.name not in SUBAGENT_ONLY:
                found[path.name] = first_line(path)
    return found


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


@unittest.skipIf(os.environ.get("HARNESS_NO_CLAUDE") == "1", "HARNESS_NO_CLAUDE=1: claude -p not run")
class ReinjectTests(unittest.TestCase):
    def setUp(self):
        self.claude = shutil.which("claude")
        self.assertIsNotNone(self.claude, "claude CLI not on PATH; run_tests.py --no-claude skips this test")
        self.tmp = Path(tempfile.mkdtemp(prefix="rules-reinject-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cwd = self.tmp / "cwd"
        self.cwd.mkdir()
        # Claude Code reads CLAUDE.md from every folder above the cwd, and the temp dir can sit in a home
        # that holds the real one. The sentinel proves the exclusion works.
        (self.tmp / "CLAUDE.md").write_text(ANCESTOR_SENTINEL + "\n", encoding="utf-8")
        self.settings = self.tmp / "settings.json"
        # Only worktree_root.sh can put this line into a request: the value exists nowhere else.
        root = "work\\trees-" + uuid.uuid4().hex
        self.worktree_line = "Worktree root: " + root
        self.settings.write_text(json.dumps({"claudeMdExcludes": ["**/CLAUDE.md"],
                                             "env": {"HARNESS_WORKTREE_ROOT": root}}), encoding="utf-8")
        self.session = str(uuid.uuid4())
        self.turn = 0

    def claude_turn(self, api, prompt, *args):
        self.turn += 1
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "ANTHROPIC_", "HARNESS_"))}
        env.update(
            CLAUDE_CONFIG_DIR=str(self.tmp / "cfg"),
            ANTHROPIC_BASE_URL=api.url,
            ANTHROPIC_API_KEY="stub-key",
            HOME=str(self.tmp),
            USERPROFILE=str(self.tmp),
        )
        command = [self.claude, "-p", prompt, *args, "--model", "haiku", "--output-format", "json",
                   "--settings", str(self.settings), "--debug-file", str(self.debug_log(self.turn))]
        for plugin in INJECTED:
            command += ["--plugin-dir", str(PLUGINS / plugin)]
        proc = subprocess.run(command, cwd=str(self.cwd), env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=40)
        self.assertEqual(proc.returncode, 0, proc.stdout.decode("utf-8", "replace"))

    def debug_log(self, turn):
        return self.tmp / ("debug-%d.log" % turn)

    def debug_text(self, turn):
        return self.debug_log(turn).read_text(encoding="utf-8", errors="replace")

    def copies(self, api, word):
        """{rules file: copies} in the one request whose prompt carries the unique word."""
        requests = [body for body in api.requests if any(word in text for text in strings(body.get("messages")))]
        self.assertEqual(len(requests), 1, "requests that carry %s" % word)
        texts = list(strings(requests[0]))
        self.assertFalse([text for text in texts if ANCESTOR_SENTINEL in text], "a CLAUDE.md above the cwd leaked in")
        lines = Counter(line for text in texts for line in text.splitlines())
        counts = {name: lines[line] for name, line in markers().items()}
        # A hook's first line arrives as "SessionStart:<source> hook success: <line>".
        counts["worktree_root.sh"] = sum(n for line, n in lines.items() if line.endswith(self.worktree_line))
        return counts

    def assert_one_copy_each(self, counts):
        self.assertEqual(len(counts), 19)
        self.assertEqual({name: count for name, count in counts.items() if count != 1}, {})

    def test_compact_leaves_one_copy(self):
        first = "probe-" + uuid.uuid4().hex
        last = "probe-" + uuid.uuid4().hex
        with StubApi() as api:
            self.claude_turn(api, first, "--session-id", self.session)
            self.assert_one_copy_each(self.copies(api, first))
            self.claude_turn(api, "/compact", "--resume", self.session)
            self.claude_turn(api, last, "--resume", self.session)
            self.assert_one_copy_each(self.copies(api, last))
        self.assertIn("SessionStart:compact", self.debug_text(2))
        self.assert_no_rules_handler_ran_on_resume(3)

    def test_resume_leaves_one_copy(self):
        first = "probe-" + uuid.uuid4().hex
        last = "probe-" + uuid.uuid4().hex
        with StubApi() as api:
            self.claude_turn(api, first, "--session-id", self.session)
            self.claude_turn(api, last, "--resume", self.session)
            self.assert_one_copy_each(self.copies(api, last))
        self.assert_no_rules_handler_ran_on_resume(2)

    def assert_no_rules_handler_ran_on_resume(self, turn):
        """Under resume, identical hook text stays one copy and differing text adds one per resume, so the
        request count alone cannot see a rules hook that fired. The rules hooks do not fire on resume because
        the matcher omits it: the debug log shows that, and the unmatched inbox hook proves it records
        resume hooks."""
        ran = [line for line in self.debug_text(turn).splitlines() if "Hooks: SessionStart:resume [" in line]
        self.assertEqual([line for line in ran if any(name in line for name in
                                                      ("rules_start.sh", "talk_start.sh", "worktree_root.sh"))], [])
        self.assertEqual(len([line for line in ran if "lessons_start.py" in line]), 1, ran)


if __name__ == "__main__":
    unittest.main(verbosity=2)
