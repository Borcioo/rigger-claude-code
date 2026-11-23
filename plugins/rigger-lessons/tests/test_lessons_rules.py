"""Tests for rules/lessons.md and its subagent short form, as rigger-core's assembler prints them."""

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
CORE = PLUGIN.parent / "rigger-core"  # the repository layout; installed, rigger-core is found through its own root
ASSEMBLE = CORE / "tools" / "assemble" / "assemble.py"


def load_hooks():
    with open(HOOKS_JSON, encoding="utf-8") as handle:
        return json.load(handle)["hooks"]


class LessonsRulesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lessons-rules-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def render(self, *args):
        """rigger-core's assembler with this plugin as the only listed module, a temp config dir and HOME."""
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        env.update({"HOME": self.tmp, "CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "config"),
                    "CLAUDE_PLUGIN_ROOT": CORE.as_posix(), "RIGGER_MODULE_DIRS": str(PLUGIN)})
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(ASSEMBLE), "--render"] + list(args),
                              stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env, cwd=self.tmp, timeout=60)
        self.assertEqual((proc.returncode, proc.stderr), (0, b""))
        return proc.stdout

    def test_rules_come_from_the_manifest_and_no_rules_handler_is_left(self):
        with open(PLUGIN / "rigger-module.json", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle)["rules"], [{"file": "rules/lessons.md", "audience": "main"},
                                                          {"file": "rules/lessons-subagent.md", "audience": "subagent"}])
        hooks = load_hooks()
        self.assertNotIn("SubagentStart", hooks)
        self.assertEqual(hooks["SessionStart"], [{"hooks": [{
            "type": "command", "timeout": 10,
            "command": 'sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" --notice "${CLAUDE_PLUGIN_ROOT}/hooks/lessons_start.py"'}]},
            {"matcher": "startup|clear", "hooks": [{
                "type": "command", "timeout": 10,
                "command": 'sh "${CLAUDE_PLUGIN_ROOT}/hooks/core_check.sh" rigger-lessons'}]}])

    def test_session_prints_the_root_line_and_the_file(self):
        out = self.render()
        expected = ("Plugin root: %s\n" % PLUGIN.as_posix()).encode("utf-8")
        expected += (PLUGIN / "rules" / "lessons.md").read_bytes()
        self.assertIn(expected, out)
        self.assertNotIn(b"## Lessons (subagents)", out)

    def test_subagent_gets_the_short_form_only(self):
        out = self.render("--subagent", "--agent-type", "rigger-core:worker")
        text = (PLUGIN / "rules" / "lessons-subagent.md").read_bytes()
        self.assertIn(("Plugin root: %s\n" % PLUGIN.as_posix()).encode("utf-8") + text, out)
        self.assertNotIn((PLUGIN / "rules" / "lessons.md").read_bytes().split(b"\n")[0], out)
        self.assertIn(b'under "Lessons"', text)

    def test_subagent_start_skips_agents_without_claude_md(self):
        for agent in ("Explore", "Plan", "rigger-core:implementer", "rigger-core:mapper"):
            with self.subTest(agent=agent):
                self.assertEqual(self.render("--subagent", "--agent-type", agent), b"")

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
