"""The rules every module prints: the rigger-core part handlers that carry them, and what they say."""

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
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_rules_hooks as common  # noqa: E402

PLUGINS = common.REPO / "plugins"
CORE = PLUGINS / "rigger-core"
sys.path.insert(0, str(CORE / "tools" / "assemble"))

import assemble  # noqa: E402

MATCHER = common.MATCHER
SESSION_PARTS = 6
SUBAGENT_PARTS = 4
SKILL_TABLES = {
    "rigger-windows": "windows-skills.md",
    "rigger-wp": "wp-skills.md",
}
WINDOWS = PLUGINS / "rigger-windows"
ROOT_NAME_LINE = "The `Plugin root:` line above is `<rigger-windows root>`."
SHELL_LINE = "**the PowerShell tool is the default shell**"
TABLE_HEADER = "| Before | Skill |\n|---|---|\n"
# Every session loads these sections: they may be rewritten, never grown. Lower the ceiling when they shrink.
# The sum is the rendered general layer with module options at their defaults (D20).
RULES_CEILING_BYTES = 27708
RULES_MIN_FILES = 19
WORKERS = ["rigger-core:worker", "rigger-core:reviewer", "general-purpose"]
TESTING_SKILL = PLUGINS / "rigger-core" / "skills" / "testing" / "SKILL.md"
ROW_RE = re.compile(r"\| .+ \| `([a-z-]+):([a-z-]+)` \|")
SID = "5b1f0c1e-7d3a-4c55-9a2e-0f6d1c2b3a49"
FILL = 8400  # one fill section per part: two never share the 9,000-byte cap


def load_hooks(plugin):
    with open(PLUGINS / plugin / "hooks" / "hooks.json", encoding="utf-8") as handle:
        return json.load(handle)["hooks"]


def load_manifest(root):
    with open(Path(root) / "rigger-module.json", encoding="utf-8") as handle:
        return json.load(handle)


def module_dirs(root=PLUGINS):
    return [path.parent for path in sorted(Path(root).glob("*/rigger-module.json"))]


def part_command(index, subagent=False):
    return 'sh "${CLAUDE_PLUGIN_ROOT}/tools/assemble/part.sh" %d%s' % (index, " --subagent" if subagent else "")


def fires(group, source):
    matcher = group.get("matcher", "")
    return matcher in ("", "*") or re.fullmatch(matcher, source) is not None


def session_handlers(plugin, source):
    """Every SessionStart handler of a plugin that a session of this source runs."""
    return [handler for group in load_hooks(plugin)["SessionStart"] if fires(group, source) for handler in group["hooks"]]


def without_root_lines(data):
    return b"".join(line for line in data.splitlines(True) if not line.startswith(b"Plugin root: "))


def root_line(root):
    return ("Plugin root: %s\n" % Path(root).as_posix()).encode("utf-8")


def heading_lines(text):
    return [line.strip(" \t\r") for line in text.split("\n") if line.startswith("## ")]


class Assembled:
    """The repository's modules assembled in-process with an empty config dir: options at their defaults."""

    def __init__(self, root=PLUGINS, **environ):
        self.root = Path(root)
        tmp = Path(tempfile.mkdtemp(prefix="plugin-rules-"))
        try:
            (tmp / "config").mkdir()
            (tmp / "project").mkdir()
            env = {"CLAUDE_CONFIG_DIR": str(tmp / "config"),
                   "RIGGER_MODULE_DIRS": os.pathsep.join(str(path) for path in module_dirs(self.root))}
            env.update(environ)
            inputs = assemble.Inputs(config_dir=tmp / "config", project_dir=tmp / "project", environ=env,
                                     self_root=self.root / "rigger-core")
            self.main = assemble.assemble(inputs, "main")
            self.subagent = assemble.assemble(inputs, "subagent", "general-purpose")
        finally:
            shutil.rmtree(str(tmp), True)

    def text(self, plugin, name, audience="main"):
        """The rendered text of one rules file, as the given audience gets it."""
        found = self.main if audience == "main" else self.subagent
        return b"".join(block[1] for block, section in zip(found.blocks, found.sections)
                        if section["module"] == plugin and section["file"] == "rules/" + name)

    def whole(self, audience="main"):
        return assemble.whole_text(self.main if audience == "main" else self.subagent)


_ASSEMBLED = []


def assembled():
    if not _ASSEMBLED:
        _ASSEMBLED.append(Assembled())
    return _ASSEMBLED[0]


def rules_text(plugin, name, audience="main"):
    return assembled().text(plugin, name, audience)


class HookRun(unittest.TestCase):
    """Runs rigger-core's real part commands through sh, as Claude Code does, with a temp config dir."""

    def setUp(self):
        self.sh = common.find_sh()
        self.assertIsNotNone(self.sh, "no POSIX sh found (Windows needs Git for Windows)")
        self.tmp = Path(tempfile.mkdtemp(prefix="plugin-rules-hooks-"))
        self.addCleanup(shutil.rmtree, str(self.tmp), True)
        self.config = self.tmp / "config"
        self.config.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()

    def run_parts(self, event, dirs, stdin, **extra):
        group = load_hooks("rigger-core")[event]
        commands = [handler["command"] for g in group for handler in g["hooks"] if "part.sh" in handler["command"]]
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("CLAUDE_", "HARNESS_", "RIGGER_"))}
        # HOME alone: a Store Python derives its own path from USERPROFILE.
        env.update({"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.config),
                    "CLAUDE_PLUGIN_ROOT": CORE.as_posix(), "CLAUDE_PLUGIN_DATA": (self.tmp / "data").as_posix(),
                    "RIGGER_MODULE_DIRS": os.pathsep.join(str(path) for path in dirs)})
        env["PATH"] = os.pathsep.join([str(Path(self.sh).parent), env.get("PATH", "")])
        env.update(extra)
        outputs = []
        for command in commands:
            proc = subprocess.run([self.sh, "-c", command], input=stdin, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, env=env, cwd=str(self.tmp), timeout=60)
            self.assertEqual((proc.returncode, proc.stderr), (0, b""), command)
            self.assertNotIn(b"\r", proc.stdout)
            outputs.append(proc.stdout)
        return outputs

    def session(self, dirs, source="startup", **extra):
        stdin = json.dumps({"session_id": SID, "cwd": str(self.tmp), "hook_event_name": "SessionStart",
                            "source": source}).encode("utf-8")
        return self.run_parts("SessionStart", dirs, stdin, **extra)

    def subagent(self, dirs, agent="general-purpose", **extra):
        stdin = json.dumps({"session_id": SID, "cwd": str(self.tmp), "hook_event_name": "SubagentStart",
                            "agent_type": agent}).encode("utf-8")
        return self.run_parts("SubagentStart", dirs, stdin, **extra)

    def context(self, out):
        if out == b"":
            return b""
        data = json.loads(out.decode("utf-8"))
        self.assertEqual(data["hookSpecificOutput"]["hookEventName"], "SubagentStart")
        return data["hookSpecificOutput"]["additionalContext"].encode("utf-8")


class WiringTests(HookRun):
    def test_every_rules_file_of_a_module_is_in_its_manifest(self):
        dirs = module_dirs()
        self.assertEqual(len(dirs), 7)
        files = 0
        for root in dirs:
            with self.subTest(module=root.name):
                listed = sorted(entry["file"] for entry in load_manifest(root)["rules"])
                self.assertEqual(listed, sorted("rules/" + path.name for path in (root / "rules").glob("*.md")))
                files += len(listed)
        self.assertGreaterEqual(files, RULES_MIN_FILES)

    def test_core_runs_six_session_parts_and_four_subagent_parts(self):
        hooks = load_hooks("rigger-core")
        groups = [group for group in hooks["SessionStart"] if any("part.sh" in h["command"] for h in group["hooks"])]
        self.assertEqual(groups, [{"matcher": MATCHER, "hooks": [
            {"type": "command", "command": part_command(i), "timeout": 10} for i in range(1, SESSION_PARTS + 1)]}])
        self.assertEqual(hooks["SubagentStart"], [{"hooks": [
            {"type": "command", "command": part_command(i, True), "timeout": 10}
            for i in range(1, SUBAGENT_PARTS + 1)]}])

    def test_core_groups_stay_independent(self):
        harness_start = ('sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" --notice -S '
                         '"${CLAUDE_PLUGIN_ROOT}/tools/harness_start/harness_start.py" || true')
        compact_guard = ('sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S '
                         '"${CLAUDE_PLUGIN_ROOT}/tools/context-guard/compact_guard.py" start || true')
        parts = [part_command(i) for i in range(1, SESSION_PARTS + 1)]
        for source, commands in (
            ("startup", [harness_start] + parts),
            ("compact", [compact_guard] + parts),
            ("clear", parts),
            ("resume", []),
        ):
            with self.subTest(source=source):
                self.assertEqual([h["command"] for h in session_handlers("rigger-core", source)], commands)

    def fill_module(self, name, count, audience):
        """A module whose every section fills a part of its own."""
        root = self.tmp / "modules" / name
        rules = []
        for index in range(1, count + 1):
            path = root / "rules" / ("%s%d.md" % (name, index))
            common.write_bytes(path, "## %s %d\n%s\n" % (name, index, "x" * FILL))
            rules.append({"file": "rules/" + path.name, "audience": audience})
        common.write_bytes(root / ".claude-plugin" / "plugin.json", json.dumps({"name": name}))
        common.write_bytes(root / "rigger-module.json", json.dumps(
            {"schema": 1, "name": name, "layer": "general", "fills": None, "rules": rules, "triggers": []}))
        return root

    def test_every_part_the_assembler_fills_has_a_handler(self):
        # Core's state file takes part 1, so five main fills reach part 6; four subagent fills reach part 4.
        dirs = [self.fill_module("mainfill", SESSION_PARTS - 1, "main"),
                self.fill_module("subfill", SUBAGENT_PARTS, "subagent")]
        session = self.session(dirs)
        self.assertEqual(len(session), SESSION_PARTS)
        self.assertNotIn(b"", session)
        joined = b"".join(session)
        self.assertNotIn(b"left out over the hook cap", joined)
        self.assertEqual(joined.count(b"x" * FILL), SESSION_PARTS - 1)
        subagent = [self.context(out) for out in self.subagent(dirs)]
        self.assertEqual(len(subagent), SUBAGENT_PARTS)
        self.assertNotIn(b"", subagent)
        joined = b"".join(subagent)
        self.assertNotIn(b"left out over the hook cap", joined)
        self.assertEqual(joined.count(b"x" * FILL), SUBAGENT_PARTS)


class SessionStartTests(HookRun):
    def expected_headings(self, audience):
        found = []
        for root in module_dirs():
            for entry in load_manifest(root)["rules"]:
                if entry["audience"] in ("both", audience):
                    text = assembled().text(root.name, entry["file"][len("rules/"):], audience).decode("utf-8")
                    found.extend(heading_lines(text))
        self.assertGreaterEqual(len(found), 17)  # 18 main-session files; 00-scope.md is a preamble, no heading
        return found

    def test_each_source_prints_each_section_once_after_its_root_line(self):
        dirs = module_dirs()
        whole = without_root_lines(assembled().whole())
        for source in ("startup", "clear", "compact"):
            with self.subTest(source=source):
                joined = b"".join(self.session(dirs, source))
                self.assertEqual(without_root_lines(joined), whole)
                lines = joined.decode("utf-8").split("\n")
                for heading in self.expected_headings("main"):
                    self.assertEqual(lines.count(heading), 1, heading)
                for root in dirs:
                    if root.name != "rigger-panel":
                        self.assertIn(root_line(root), joined)

    def test_resume_prints_nothing(self):
        self.assertEqual(self.session(module_dirs(), "resume"), [b""] * SESSION_PARTS)


class SubagentStartTests(HookRun):
    def test_a_worker_gets_the_root_line_and_the_shell_line(self):
        for agent in WORKERS:
            with self.subTest(agent=agent):
                text = b"".join(self.context(out) for out in self.subagent(module_dirs(), agent)).decode("utf-8")
                self.assertIn(root_line(WINDOWS).decode("utf-8") + ROOT_NAME_LINE + "\n", text)
                self.assertIn(SHELL_LINE, text)
                self.assertEqual(without_root_lines(text.encode("utf-8")),
                                 without_root_lines(assembled().whole("subagent")))

    def test_a_worker_gets_each_skill_table(self):
        text = assembled().whole("subagent").decode("utf-8")
        for plugin, name in SKILL_TABLES.items():
            with self.subTest(plugin=plugin):
                self.assertIn(TABLE_HEADER, rules_text(plugin, name, "subagent").decode("utf-8"))
                self.assertIn(rules_text(plugin, name, "subagent").decode("utf-8"), text)

    def test_skipped_agents_get_nothing(self):
        for agent in common.SKIPPED_AGENTS:
            with self.subTest(agent=agent):
                self.assertEqual(self.subagent(module_dirs(), agent), [b""] * SUBAGENT_PARTS)


class DropTests(unittest.TestCase):
    def test_a_basename_drops_only_its_own_file(self):
        found = Assembled(HARNESS_RULES_DROP="windows.md")
        self.assertEqual(found.text("rigger-windows", "windows.md"), b"")
        self.assertEqual(found.text("rigger-windows", "windows-skills.md"), rules_text("rigger-windows", "windows-skills.md"))

    def test_root_name_stays_when_the_shell_heading_is_dropped(self):
        heading = heading_lines(rules_text("rigger-windows", "windows.md").decode("utf-8"))[0]
        text = Assembled(HARNESS_RULES_DROP=heading).whole("subagent").decode("utf-8")
        self.assertNotIn(heading + "\n", text)
        self.assertIn(root_line(WINDOWS).decode("utf-8") + ROOT_NAME_LINE + "\n\n", text)


class SkillTableTests(unittest.TestCase):
    def test_each_table_lists_the_plugin_skills_by_installed_name(self):
        for plugin, name in SKILL_TABLES.items():
            with self.subTest(plugin=plugin):
                text = rules_text(plugin, name).decode("utf-8")
                self.assertIn(TABLE_HEADER, text)
                rows = text.split(TABLE_HEADER)[1].splitlines()
                matches = [ROW_RE.fullmatch(row) for row in rows]
                self.assertNotIn(None, matches, rows)
                self.assertEqual({match.group(1) for match in matches}, {plugin})
                skills = sorted(path.parent.name for path in (PLUGINS / plugin / "skills").glob("*/SKILL.md"))
                self.assertGreater(len(skills), 0)
                self.assertEqual(sorted(match.group(2) for match in matches), skills)

    def test_the_core_table_keeps_the_header_the_others_repeat(self):
        self.assertIn(TABLE_HEADER, rules_text("rigger-rules", "skills.md").decode("utf-8"))

    def test_every_table_is_a_placeholder_in_its_rules_file(self):
        for plugin, name in dict(SKILL_TABLES, **{"rigger-rules": "skills.md"}).items():
            with self.subTest(plugin=plugin):
                raw = (PLUGINS / plugin / "rules" / name).read_text(encoding="utf-8")
                self.assertNotIn("| Before | Skill |", raw)
                self.assertEqual(len(re.findall(r"^\{\{triggers:[^{}\s]+\}\}$", raw, re.M)), 1)


class CrossPluginWordingTests(unittest.TestCase):
    """A rule that needs something outside rigger-rules says when it applies."""

    def text(self, name):
        return " ".join(rules_text("rigger-rules", name).decode("utf-8").split())

    def test_the_lessons_archive_is_conditional_and_shell_neutral(self):
        text = self.text("verify.md")
        self.assertIn("in the folder the `HARNESS_LESSONS_ARCHIVE` environment variable names, when it is set.", text)
        self.assertNotIn("$HARNESS_LESSONS_ARCHIVE", text)

    def test_safe_edit_is_conditional_and_the_worktree_route_is_not(self):
        self.assertIn(
            "Red runs: in a worktree, or in place only through `safe-edit.ps1 -Run` (needs rigger-windows).",
            self.text("tests.md"))
        self.assertIn(
            "go through the Edit tool or `safe-edit.ps1 -Find -Replace -Count` (literal, counted; needs rigger-windows)",
            self.text("defects.md"))

    def test_only_a_main_session_loads_the_git_skill(self):
        self.assertIn(
            "`rigger-core:git-workflow`: a main session loads it before the first git write; a subagent gets its "
            "name in the brief.", self.text("git.md"))

    def test_every_committed_change_is_made_in_a_worktree(self):
        self.assertIn(
            "- Every change that ends in a commit is made in its own worktree where the app's Worktree location "
            "setting puts it, else under the worktree root the SessionStart line names, else in "
            "`.claude/worktrees/` inside the repo (git-ignored), on a `feature/`, `fix/` or "
            "`chore/` branch from the default branch, and reaches it through a pull request (a squash merge "
            "where the repo has no remote); the main checkout stays on the default branch and is only read and "
            "merged into. Exception: a project whose running app or container serves the main checkout (its "
            "CLAUDE.md or `.claude/environments.json` says so) is edited there, one writer at a time. "
            "Remove the worktree after the merge.", self.text("git.md"))


class StateFileTests(unittest.TestCase):
    def test_the_bullet_is_under_its_own_heading_and_is_for_the_main_session(self):
        lines = rules_text("rigger-core", "state-file.md").decode("utf-8").split("\n")
        self.assertEqual(heading_lines("\n".join(lines)), ["## State file for long sessions"])
        self.assertEqual(lines[1], "- **Long sessions keep a state file; they never hand off.** Main session only (subagents never")
        self.assertEqual(lines[11:13], [
            "  handoff.",
            "- **The state file stays under 9 000 characters,** all that comes back after compaction."])
        self.assertNotIn("coordinator", "\n".join(lines))
        self.assertEqual(lines[-2:], ["  from the state file.", ""])
        self.assertEqual(rules_text("rigger-core", "state-file.md", "subagent"), b"")


class PlanStopTests(unittest.TestCase):
    """The stop values the plan-active guard in side_session_guard.py accepts, named in the plans rule."""

    def plans(self):
        return " ".join(rules_text("rigger-rules", "plans-main.md").decode("utf-8").split())

    def test_the_plan_rule_writes_plan_and_lists_every_stop_value(self):
        text = self.plans()
        self.assertIn("Write `plan: <plan path>` into the state file frontmatter", text)
        self.assertIn("the plan is my OK up to merges into the integration branch", text)
        self.assertIn("set `stop:` to `main` (any merge into the default branch)", text)
        stops = re.search(r"set `stop:` to (.+?), then ask", text)
        self.assertIsNotNone(stops)
        self.assertEqual(re.findall(r"`([a-z-]+)`", stops.group(1)),
                         ["main", "release", "force-push", "prod-data", "secrets", "goal-unreachable", "app-refused"])
        self.assertIn("sets `stop: done`", text)
        self.assertIn("then ask; clear `stop:` once I answer", text)
        self.assertIn("my answer, my stop or a new request removes `plan:` and `stop:`", text)

    def test_the_rule_names_exactly_the_stop_values_the_guard_accepts(self):
        guard = (PLUGINS / "rigger-core" / "tools" / "side-session-guard" / "side_session_guard.py").read_text(encoding="utf-8")
        accepted = set(re.findall(r'"([a-z-]+)"', re.search(r"STOPS = frozenset\(\((.+?)\)\)", guard, re.S).group(1)))
        named = set(re.findall(r"`([a-z-]+)`", re.search(r"set `stop:` to (.+?), then ask", self.plans()).group(1)))
        self.assertEqual(accepted, named | {"done"})

    def test_the_brainstorming_route_comes_from_the_process_slots(self):
        self.assertIn("(with superpowers installed: `superpowers:brainstorming`, then `superpowers:writing-plans`)",
                      self.plans())
        raw = (PLUGINS / "rigger-rules" / "rules" / "plans-main.md").read_text(encoding="utf-8")
        self.assertIn("{{slot:process.brainstorm}}, then {{slot:process.plans}}", raw)

    def test_the_state_file_rule_names_the_plan_and_stop_keys(self):
        text = " ".join(rules_text("rigger-core", "state-file.md").decode("utf-8").split())
        self.assertIn("`updated: <ISO time>` / `plan:`, `stop:` when set / `---`", text)


class TestingSkillTriggerTests(unittest.TestCase):
    """Where the TDD skill is loaded, the testing skill that overrides it is loaded too."""

    def test_the_testing_skill_rides_every_tdd_trigger(self):
        # The Skill cell (the last one) of every row that loads TDD loads the testing skill first.
        rows = [line.split("|") for line in rules_text("rigger-rules", "skills.md").decode("utf-8").splitlines()
                if line.startswith("|")]
        cells = [row[-2] for row in rows if "superpowers:test-driven-development" in row[-2]]
        self.assertTrue(cells, "no TDD row read from skills.md")
        for cell in cells:
            self.assertIn("`rigger-core:testing`, then `superpowers:test-driven-development`", cell)
        worker = " ".join((PLUGINS / "rigger-core" / "agents" / "worker.md").read_text(encoding="utf-8").split())
        self.assertIn("Load `rigger-core:testing` before writing code or tests and, where superpowers is "
                      "installed, `superpowers:test-driven-development` after it", worker)
        lines = TESTING_SKILL.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[:4], ["---", "name: testing", "description: >-",
                                     "  Load BEFORE writing, changing or reviewing tests or a red run: a feature or bugfix test, a test"])


class RulesSizeTests(unittest.TestCase):
    def test_rules_text_stays_under_the_ceiling(self):
        root = Path(os.environ.get("RIGGER_RULES_ROOT") or PLUGINS)
        found = Assembled(root)
        self.assertEqual(found.main.notices + found.subagent.notices, [], "rules assembled from %s" % root)
        main_ids = {section["id"] for section in found.main.sections}
        blocks = list(found.main.blocks) + [block for block, section in zip(found.subagent.blocks, found.subagent.sections)
                                            if section["id"] not in main_ids]
        files = {(s["module"], s["file"]) for s in found.main.sections + found.subagent.sections}
        self.assertGreaterEqual(len(files), RULES_MIN_FILES, "rules files read from %s" % root)
        self.assertLessEqual(sum(len(block[1]) for block in blocks), RULES_CEILING_BYTES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
