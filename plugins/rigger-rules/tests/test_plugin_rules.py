"""The rules of rigger-core, rigger-windows, and rigger-wp: wiring and what each hook prints."""

import json
import re
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_rules_hooks as common  # noqa: E402

PLUGINS = common.REPO / "plugins"
MATCHER = common.MATCHER
SESSION_FILES = {
    "rigger-core": ["state-file.md"],
    "rigger-windows": ["windows.md", "windows-skills.md"],
    "rigger-wp": ["wp-skills.md"],
}
SKILL_TABLES = {
    "rigger-windows": "windows-skills.md",
    "rigger-wp": "wp-skills.md",
}
WINDOWS = PLUGINS / "rigger-windows"
ROOT_NAME_LINE = "The `Plugin root:` line above is `<rigger-windows root>`."
SHELL_LINE = "**the PowerShell tool is the default shell**"
TABLE_HEADER = "| Before | Skill |\n|---|---|\n"
WORKERS = ["rigger-core:worker", "rigger-core:reviewer", "general-purpose"]
ROW_RE = re.compile(r"\| .+ \| `([a-z-]+):([a-z-]+)` \|")


def load_hooks(plugin):
    with open(PLUGINS / plugin / "hooks" / "hooks.json", encoding="utf-8") as handle:
        return json.load(handle)["hooks"]


def rules_command(name):
    return 'sh "${CLAUDE_PLUGIN_ROOT}/rules/rules_start.sh" ' + name


def fires(group, source):
    matcher = group.get("matcher", "")
    return matcher in ("", "*") or re.fullmatch(matcher, source) is not None


def session_handlers(plugin, source):
    """Every SessionStart handler of a plugin that a session of this source runs."""
    return [handler for group in load_hooks(plugin)["SessionStart"] if fires(group, source) for handler in group["hooks"]]


def rules_handlers(plugin, source):
    return [handler for handler in session_handlers(plugin, source) if "rules_start.sh" in handler["command"]]


def rules_text(plugin, name):
    return (PLUGINS / plugin / "rules" / name).read_bytes()


def root_line(plugin):
    return ("Plugin root: %s\n" % (PLUGINS / plugin).as_posix()).encode("utf-8")


def windows_subagent_command():
    return load_hooks("rigger-windows")["SubagentStart"][0]["hooks"][0]["command"]


def heading_lines(text):
    return [line.strip(" \t\r") for line in text.split("\n") if line.startswith("## ")]


class WiringTests(unittest.TestCase):
    def test_every_rules_file_of_a_plugin_is_listed(self):
        for plugin, names in SESSION_FILES.items():
            with self.subTest(plugin=plugin):
                self.assertEqual(sorted(names), sorted(path.name for path in (PLUGINS / plugin / "rules").glob("*.md")))

    def test_one_session_group_with_one_handler_per_file(self):
        for plugin, names in SESSION_FILES.items():
            with self.subTest(plugin=plugin):
                groups = [group for group in load_hooks(plugin)["SessionStart"]
                          if any("rules_start.sh" in handler["command"] for handler in group["hooks"])]
                self.assertEqual(len(groups), 1)
                self.assertEqual(groups[0]["matcher"], MATCHER)
                self.assertEqual(groups[0]["hooks"], [
                    {"type": "command", "command": rules_command(name), "timeout": 10} for name in names
                ])

    def test_core_groups_stay_independent(self):
        harness_start = ('sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" --notice -S '
                         '"${CLAUDE_PLUGIN_ROOT}/tools/harness_start/harness_start.py" || true')
        compact_guard = ('sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S '
                         '"${CLAUDE_PLUGIN_ROOT}/tools/context-guard/compact_guard.py" start || true')
        state_file = rules_command("state-file.md")
        for source, commands in (
            ("startup", [harness_start, state_file]),
            ("compact", [compact_guard, state_file]),
            ("clear", [state_file]),
            ("resume", []),
        ):
            with self.subTest(source=source):
                self.assertEqual([h["command"] for h in session_handlers("rigger-core", source)], commands)

    def test_subagent_handlers_carry_the_skill_tables(self):
        self.assertNotIn("SubagentStart", load_hooks("rigger-core"))
        expected = {"rigger-windows": ["windows.md", "windows-skills.md"],
                    "rigger-wp": ["wp-skills.md"]}
        for plugin, names in expected.items():
            with self.subTest(plugin=plugin):
                self.assertEqual(load_hooks(plugin)["SubagentStart"], [{"hooks": [
                    {"type": "command", "command": rules_command("--subagent " + name), "timeout": 10}
                    for name in names
                ]}])


class SessionStartTests(common.Base):
    def test_each_source_prints_each_file_and_its_root_line_once(self):
        for plugin, names in SESSION_FILES.items():
            for source in ("startup", "clear", "compact"):
                with self.subTest(plugin=plugin, source=source):
                    root = PLUGINS / plugin
                    out = b"".join(self.run_command(h["command"], root=root) for h in rules_handlers(plugin, source))
                    self.assertEqual(out, b"".join(root_line(plugin) + rules_text(plugin, name) for name in names))

    def test_resume_prints_nothing(self):
        for plugin in SESSION_FILES:
            with self.subTest(plugin=plugin):
                self.assertEqual(rules_handlers(plugin, "resume"), [])


class SubagentStartTests(common.Base):
    def test_a_worker_gets_the_root_line_and_the_shell_line(self):
        for agent in WORKERS:
            with self.subTest(agent=agent):
                stdin = json.dumps({"agent_type": agent}).encode("utf-8")
                out = self.run_command(windows_subagent_command(), root=WINDOWS, stdin=stdin)
                text = self.context(out)
                expected = root_line("rigger-windows") + rules_text("rigger-windows", "windows.md")
                self.assertEqual(text, expected.decode("utf-8"))
                self.assertEqual(text.split("\n")[1], ROOT_NAME_LINE)
                self.assertIn(SHELL_LINE, text)
                self.assertNotIn("| Before | Skill |", text)

    def test_a_worker_gets_each_skill_table(self):
        stdin = json.dumps({"agent_type": WORKERS[0]}).encode("utf-8")
        for plugin, name in SKILL_TABLES.items():
            with self.subTest(plugin=plugin):
                out = self.run_command(rules_command("--subagent " + name), root=PLUGINS / plugin, stdin=stdin)
                self.assertIn(TABLE_HEADER, self.context(out))

    def test_skipped_agents_get_nothing(self):
        for agent in common.SKIPPED_AGENTS:
            with self.subTest(agent=agent):
                stdin = json.dumps({"agent_type": agent}).encode("utf-8")
                self.assertEqual(self.run_command(windows_subagent_command(), root=WINDOWS, stdin=stdin), b"")


class DropTests(common.Base):
    def test_a_basename_silences_only_its_own_handler(self):
        for plugin, names in SESSION_FILES.items():
            for name in names:
                for handler in rules_handlers(plugin, "startup"):
                    with self.subTest(file=name, command=handler["command"]):
                        out = self.run_command(handler["command"], root=PLUGINS / plugin, HARNESS_RULES_DROP=name)
                        printed = handler["command"].rsplit(" ", 1)[1]
                        expected = b"" if printed == name else root_line(plugin) + rules_text(plugin, printed)
                        self.assertEqual(out, expected)

    def test_a_heading_removes_its_section_and_keeps_what_is_above_it(self):
        for plugin, names in SESSION_FILES.items():
            for handler, name in zip(rules_handlers(plugin, "startup"), names):
                text = rules_text(plugin, name).decode("utf-8")
                headings = heading_lines(text)
                with self.subTest(file=name):
                    self.assertEqual(len(headings), 1)
                    out = self.run_command(handler["command"], root=PLUGINS / plugin, HARNESS_RULES_DROP=headings[0])
                    above = text[: text.index(headings[0])]
                    self.assertEqual(out, root_line(plugin) + above.encode("utf-8"))

    def test_root_name_stays_when_the_shell_heading_is_dropped(self):
        text = rules_text("rigger-windows", "windows.md").decode("utf-8")
        out = self.run_command(windows_subagent_command(), root=WINDOWS, stdin=b"{}",
                               HARNESS_RULES_DROP=heading_lines(text)[0])
        self.assertEqual(self.context(out).split("\n")[1:], [ROOT_NAME_LINE, "", ""])

    def test_a_basename_silences_the_subagent_handler(self):
        out = self.run_command(windows_subagent_command(), root=WINDOWS, stdin=b"{}", HARNESS_RULES_DROP="windows.md")
        self.assertEqual(out, b"")


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
            "Red runs: in a worktree, or in place only through `<rigger-windows root>/tools/safe-edit/safe-edit.ps1 -Run` "
            "with rigger-windows installed (usage in its header).", self.text("tests.md"))
        self.assertIn(
            "go through the Edit tool or `safe-edit.ps1 -Find -Replace -Count` (literal, counted; needs rigger-windows)",
            self.text("defects.md"))

    def test_only_a_main_session_loads_the_git_skill(self):
        self.assertIn(
            "`rigger-core:git-workflow`: a main session loads it before the first git write; a subagent gets its "
            "name in the brief.", self.text("git.md"))

    def test_every_committed_change_is_made_in_a_worktree(self):
        self.assertIn(
            "- Every change that ends in a commit is made in its own worktree under the worktree root the "
            "SessionStart line names (unset: next to the repo, never inside it), on a `feature/`, `fix/` or "
            "`chore/` branch from the default branch, and reaches it through a pull request (a squash merge "
            "where the repo has no remote); the main checkout stays on the default branch and is only read and "
            "merged into. Exception: a project whose running app or container serves the main checkout (its "
            "CLAUDE.md or `.claude/environments.json` says so) is edited there, one writer at a time. "
            "Remove the worktree after the merge.", self.text("git.md"))


class StateFileTests(unittest.TestCase):
    def test_the_bullet_is_under_its_own_heading_and_is_for_the_main_session(self):
        lines = rules_text("rigger-core", "state-file.md").decode("utf-8").split("\n")
        self.assertEqual(heading_lines("\n".join(lines)), ["## State file for long sessions"])
        self.assertEqual(lines[2], "- **Long sessions keep a state file; they never hand off.** Main session only (subagents never")
        self.assertEqual(lines[12:16], [
            "  handoff.",
            "  A coordinator rewrites the file with `true` before every turn end in which it waits for",
            "  a side session or an agent, and sets `false` only around such a step, back to `true` right after.",
            "- **The state file stays under 9 000 characters,** all that comes back after compaction."])
        self.assertEqual(lines[-2:], ["  from the state file.", ""])


if __name__ == "__main__":
    unittest.main(verbosity=2)
