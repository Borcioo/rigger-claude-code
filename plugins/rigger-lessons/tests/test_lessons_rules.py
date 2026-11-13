"""Tests for the rules handlers in hooks/hooks.json: rules/lessons.md and its subagent short form."""

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True

PLUGIN = Path(__file__).resolve().parent.parent
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"
MATCHER = "startup|clear|compact"


def find_sh():
    if os.name == "nt":
        git_sh = Path(os.environ["ProgramFiles"], "Git", "usr", "bin", "sh.exe")
        return str(git_sh) if git_sh.is_file() else shutil.which("sh")
    return "/bin/sh"


def load_hooks():
    with open(HOOKS_JSON, encoding="utf-8") as handle:
        return json.load(handle)["hooks"]


class LessonsRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lessons-rules-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.sh = find_sh()
        self.assertIsNotNone(self.sh, "no POSIX sh found (Windows needs Git for Windows)")

    def run_command(self, command, stdin=b"{}"):
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_"))}
        env["HOME"] = env["USERPROFILE"] = self.tmp
        env["CLAUDE_PLUGIN_ROOT"] = PLUGIN.as_posix()
        env["PATH"] = os.pathsep.join([str(Path(self.sh).parent), env.get("PATH", "")])
        proc = subprocess.run([self.sh, "-c", command], input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, cwd=self.tmp, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        return proc.stdout

    def test_session_start_group(self):
        groups = [group for group in load_hooks()["SessionStart"] if "matcher" in group]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["matcher"], MATCHER)
        self.assertEqual(
            [handler["command"] for handler in groups[0]["hooks"]],
            ['sh "${CLAUDE_PLUGIN_ROOT}/rules/rules_start.sh" lessons.md'],
        )

    def test_session_start_prints_exactly_the_root_line_and_the_file(self):
        group = next(group for group in load_hooks()["SessionStart"] if "matcher" in group)
        expected = ("Plugin root: %s\n" % PLUGIN.as_posix()).encode("utf-8")
        expected += (PLUGIN / "rules" / "lessons.md").read_bytes()
        self.assertEqual(self.run_command(group["hooks"][0]["command"]), expected)

    def test_subagent_start_has_no_matcher(self):
        groups = load_hooks()["SubagentStart"]
        self.assertEqual(len(groups), 1)
        self.assertNotIn("matcher", groups[0])
        self.assertEqual(len(groups[0]["hooks"]), 1)

    def test_subagent_start_prints_the_short_form_as_json(self):
        command = load_hooks()["SubagentStart"][0]["hooks"][0]["command"]
        out = json.loads(self.run_command(command, b'{"agent_type":"rigger-core:worker"}').decode("utf-8"))
        text = (PLUGIN / "rules" / "lessons-subagent.md").read_text(encoding="utf-8")
        self.assertEqual(
            out,
            {"hookSpecificOutput": {"hookEventName": "SubagentStart",
                                    "additionalContext": "Plugin root: %s\n%s" % (PLUGIN.as_posix(), text)}},
        )
        self.assertIn('under "Lessons"', text)

    def test_subagent_start_skips_agents_without_claude_md(self):
        command = load_hooks()["SubagentStart"][0]["hooks"][0]["command"]
        for agent in ("Explore", "Plan", "rigger-core:implementer", "rigger-core:mapper"):
            with self.subTest(agent=agent):
                stdin = ('{"agent_type":"%s"}' % agent).encode("utf-8")
                self.assertEqual(self.run_command(command, stdin), b"")

    def test_global_lessons_go_to_the_rules_d_file(self):
        target = "rigger/rules.d/lessons.md"
        rules = " ".join((PLUGIN / "rules" / "lessons.md").read_text(encoding="utf-8").split())
        skill = (PLUGIN / "skills" / "curate-lessons" / "SKILL.md").read_text(encoding="utf-8")
        row = next(line for line in skill.splitlines() if line.startswith("| `global` |"))
        for name, text in (("lessons.md", rules), ("global row", row)):
            with self.subTest(text=name):
                self.assertIn("<config dir>/" + target, text)
                self.assertIn("created if missing", text)
                self.assertNotIn("plugin update", text)
                self.assertNotIn("marketplace update", text)
                self.assertNotIn("a PR", text)

    def test_issue_draft_is_optional_and_filed_only_on_the_users_ok(self):
        skill = " ".join((PLUGIN / "skills" / "curate-lessons" / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn("Borcioo/rigger-claude-code", skill)
        self.assertIn("draft the issue body into a local file", skill)
        self.assertIn("only after the user's OK", skill)

    def test_batches_are_documented_as_opt_in_with_their_cost(self):
        rules = " ".join((PLUGIN / "rules" / "lessons.md").read_text(encoding="utf-8").split())
        readme = " ".join((PLUGIN / "README.md").read_text(encoding="utf-8").split())
        self.assertIn("Background batches are off by default", rules)
        self.assertIn("`RIGGER_LESSONS_BATCH=1`", rules)
        self.assertIn("(off by default)", readme)
        self.assertIn("RIGGER_LESSONS_BATCH=1", readme)
        self.assertIn("$2 for triage and $3 for proposals per batch", readme)
        self.assertNotIn("BATCH_OFF", rules + readme)

    def test_a_rule_that_needs_rigger_rules_says_so(self):
        text = (PLUGIN / "rules" / "lessons.md").read_text(encoding="utf-8")
        self.assertIn("the way Scope of changes says (if rigger-rules is installed), and record it.", text)

    def test_the_skill_reviews_the_slice_and_parks_what_stays(self):
        skill = " ".join((PLUGIN / "skills" / "curate-lessons" / "SKILL.md").read_text(encoding="utf-8").split())
        self.assertIn('python "${CLAUDE_PLUGIN_ROOT}/tools/inbox_lint.py" --slice <inbox>', skill)
        self.assertIn("add ` · curated: <today>` to each rejected or deferred entry", skill)
        rules = (PLUGIN / "rules" / "lessons.md").read_text(encoding="utf-8")
        self.assertIn("reviews one slice of the inbox", rules)

    def test_the_rule_lists_exactly_the_signal_types_the_lint_accepts(self):
        spec = importlib.util.spec_from_file_location("inbox_lint_types", PLUGIN / "tools" / "inbox_lint.py")
        lint = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lint)
        text = " ".join((PLUGIN / "rules" / "lessons.md").read_text(encoding="utf-8").split())
        self.assertIn("`signal_type` is the evidence", text)
        start = text.index("`signal_type` is the evidence")
        sentence = text[start:text.index(".", start)]
        self.assertEqual(tuple(re.findall(r"`([a-z]+-[a-z]+)`", sentence)), lint.SIGNAL_TYPES)
        template = (PLUGIN / "templates" / "lessons-inbox.md").read_text(encoding="utf-8")
        self.assertIn("signal_type: <%s>" % "|".join(lint.SIGNAL_TYPES), template)

    def test_the_inbox_template_states_the_curated_field(self):
        self.assertIn("` · curated: YYYY-MM-DD`", (PLUGIN / "templates" / "lessons-inbox.md").read_text(encoding="utf-8"))

    def test_the_archive_readme_and_the_skill_state_the_lint_rule(self):
        readme = (PLUGIN / "templates" / "archive" / "README.md").read_text(encoding="utf-8")
        skill = " ".join((PLUGIN / "skills" / "curate-lessons" / "SKILL.md").read_text(encoding="utf-8").split())
        scope = next(line for line in readme.splitlines() if line.startswith("| `scope` |"))
        self.assertNotIn("any path through a `/.claude/` folder", scope)
        for name, text in (("README scope row", scope), ("skill", skill)):
            with self.subTest(text=name):
                self.assertIn("rigger/rules.d/lessons.md", text)
                self.assertIn("plugins/cache/", text)
                self.assertIn("plugins/marketplaces/", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
