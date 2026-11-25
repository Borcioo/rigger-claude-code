"""The review module: the reviewer agent that fills the `reviewer` slot, the `review` skill that holds its
procedure, the checklist table the skill keeps in step with `checklists/`, and the manifest that says so.
The agent's closing JSON is checked with rigger-core's `slot_check.py`, read from this repository."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

MODULE = Path(__file__).resolve().parent.parent
CORE = MODULE.parent / "rigger-core"
REVIEWER = MODULE / "agents" / "reviewer.md"
SKILL = MODULE / "skills" / "review" / "SKILL.md"
CHECKLISTS = MODULE / "checklists"
ASSEMBLE = CORE / "tools" / "assemble" / "assemble.py"
CHECKLIST_ROW = re.compile(r"^\| `checklists/([^`]+)` \| (.+) \|$", re.M)
MAX_BULLETS = 25


def flat(path):
    return " ".join(path.read_text(encoding="utf-8").split())


class ReviewerAgentTests(unittest.TestCase):
    def test_the_agent_loads_the_review_skill_first(self):
        self.assertIn("Load the `review` skill first", flat(REVIEWER))

    def test_the_fix_rounds_route_on_the_labels_the_reviewer_gives(self):
        reviewer = flat(REVIEWER)
        self.assertIn("give each finding a severity (Critical, Important, Minor)", reviewer)
        self.assertIn("mark one the base commit already had `Pre-existing`, with its evidence", reviewer)

    def test_the_reviewer_ends_with_the_contract_json(self):
        reviewer = flat(REVIEWER)
        self.assertIn("End the reply with one fenced `json` block holding the `rigger.reviewer/1` object", reviewer)
        self.assertIn("`tools/review-scope/review_scope.py --base <base> --head <head>`", reviewer)
        self.assertIn("`git diff --no-renames --name-status <base>..<head>`", reviewer)
        self.assertIn("A changed file that is neither reviewed nor skipped makes the status PARTIAL.", reviewer)
        self.assertIn("`git --no-pager diff --no-ext-diff --no-textconv --no-color <base>..<head>`", reviewer)
        self.assertIn("under a `Behaviour changes` heading of the report", reviewer)
        self.assertIn("(procedure in the testing skill the Skill triggers table names)", reviewer)
        self.assertIn("opens with `SHORTCUTS: <n>`", reviewer)
        self.assertNotIn("superpowers:", reviewer)

    def test_the_reviewer_json_example_meets_the_contract(self):
        # Shape rules only: the example's base and head are placeholders, so the git rules cannot run on it.
        text = REVIEWER.read_text(encoding="utf-8")
        self.assertIn('`"contract": "rigger.reviewer/1"`', " ".join(text.split()))
        saved = list(sys.path)
        try:
            sys.path.insert(0, str(CORE / "tools" / "slot-check"))
            import slot_check
        finally:
            sys.path[:] = saved
        verdict = slot_check.check("reviewer", text, shape_only=True)
        self.assertEqual((verdict.ok, verdict.rule, verdict.reason, verdict.findings), (True, "", "", 1))


class ReviewSkillTests(unittest.TestCase):
    def test_skill_names_the_scope_command_and_the_rules_lines(self):
        skill = flat(SKILL)
        self.assertIn("`sh '<root>/bin/py.sh' '<root>/tools/review-scope/review_scope.py' --base <base> --head <head> "
                      "--rules-out <scratch folder>`", skill)
        self.assertIn("the rigger-core plugin, whose root the rules' `Plugin root:` line gives", skill)
        self.assertIn("Read each file a `# rules` line names with the Read tool", skill)
        self.assertIn("Never run git on a path from config", skill)
        self.assertIn("every path whose verdict starts with `review`", skill)
        self.assertIn("under a `Behaviour changes` heading", skill)
        self.assertIn("`rigger.reviewer/1`", skill)
        self.assertNotIn("git show", skill)

    def test_checklist_table_matches_the_files(self):
        rows = dict(CHECKLIST_ROW.findall(SKILL.read_text(encoding="utf-8")))
        files = sorted(path.name for path in CHECKLISTS.glob("*.md"))
        self.assertEqual(sorted(rows), files)
        self.assertEqual(files, ["php.md", "python.md", "shell.md", "typescript.md"])
        for name in files:
            with self.subTest(checklist=name):
                text = (CHECKLISTS / name).read_text(encoding="utf-8")
                bullets = len(re.findall(r"^- ", text, re.M))
                self.assertGreater(bullets, 0)
                self.assertLessEqual(bullets, MAX_BULLETS)
        self.assertIn("`.py`", rows["python.md"])
        for ext in (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
            self.assertIn("`%s`" % ext, rows["typescript.md"])
        self.assertIn("`.php`", rows["php.md"])
        self.assertIn("`#!`", rows["shell.md"])


class ManifestTests(unittest.TestCase):
    def test_manifest_fills_and_provides_the_reviewer(self):
        data = json.loads((MODULE / "rigger-module.json").read_bytes().decode("utf-8"))
        self.assertEqual((data["name"], data["layer"], data["fills"], data["rules"]),
                         ("rigger-review", "general", "reviewer", []))
        self.assertEqual(data["provides"], {"reviewer": {"kind": "agent", "target": "rigger-review:reviewer",
                                                         "model": "by-rule"}})
        self.assertEqual(data["tests"], {"paths": ["tests/"], "contract": ["reviewer"]})
        self.assertEqual([(row["id"], row["load"]) for row in data["triggers"]],
                         [("rigger-review.review", ["rigger-review:review"])])
        plugin = json.loads((MODULE / ".claude-plugin" / "plugin.json").read_bytes().decode("utf-8"))
        core = json.loads((CORE / ".claude-plugin" / "plugin.json").read_bytes().decode("utf-8"))
        self.assertEqual(plugin["dependencies"], ["rigger-core"])
        for key in ("license", "homepage", "repository", "author"):
            self.assertEqual(plugin[key], core[key], key)


class SlotTests(unittest.TestCase):
    """The `reviewer` default names this module's agent; without the module the slot is empty, never filled by
    another agent (the orchestrator then reports the review blocked)."""

    def slots_row(self, roots):
        tmp = Path(tempfile.mkdtemp(prefix="review module "))
        self.addCleanup(shutil.rmtree, str(tmp), True)
        env = {key: value for key, value in os.environ.items()
               if not key.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        env.update(CLAUDE_CONFIG_DIR=str(tmp / "config"), CLAUDE_PLUGIN_ROOT=str(CORE), HOME=str(tmp),
                   RIGGER_MODULE_DIRS=os.pathsep.join(str(root) for root in roots))
        proc = subprocess.run([sys.executable, "-I", "-X", "utf8", str(ASSEMBLE), "--render"], cwd=str(tmp),
                              env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        rows = [line for line in proc.stdout.decode("utf-8").splitlines() if line.startswith("| reviewer |")]
        self.assertEqual(len(rows), 1, rows)
        return rows[0]

    def test_the_module_fills_the_reviewer_slot(self):
        self.assertEqual(self.slots_row([CORE, MODULE]), "| reviewer | agent | rigger-review:reviewer | by-rule | default |")

    def test_without_the_module_the_reviewer_slot_is_empty(self):
        self.assertFalse((CORE / "agents" / "reviewer.md").exists(), "rigger-core ships no reviewer of its own")
        self.assertEqual(self.slots_row([CORE]), "| reviewer | — | — | — | default |")


if __name__ == "__main__":
    unittest.main(verbosity=2)
