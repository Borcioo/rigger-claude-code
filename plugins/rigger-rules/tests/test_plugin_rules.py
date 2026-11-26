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
PLATFORM = PLUGINS / "rigger-platform"
SHELL_HEADING = "## Windows shell"
SHELL_LINE = "**the PowerShell tool is the default shell**"
TABLE_HEADER = "| Before | Skill |\n|---|---|\n"
# Every session loads these sections: they may be rewritten, never grown. Lower the ceiling when they shrink.
# The sum is the rendered general layer with module options at their defaults (D20).
RULES_CEILING_BYTES = 27300
SLOTS_CEILING_BYTES = 1200
SLOTS_ID = "slots.slots"
RULES_MIN_FILES = 18
# The platform module's rule requires os:windows: one rules file fewer is printed elsewhere.
PRINTED_MIN_FILES = RULES_MIN_FILES if os.name == "nt" else RULES_MIN_FILES - 1
WORKERS = ["rigger-core:worker", "rigger-review:reviewer", "general-purpose"]
TESTING_SKILL = PLUGINS / "rigger-testing" / "skills" / "testing" / "SKILL.md"
ROW_RE = re.compile(r"\| .+ \| `([a-z0-9-]+):([a-z0-9-]+)` \|")
SID = "5b1f0c1e-7d3a-4c55-9a2e-0f6d1c2b3a49"
# rigger-shellwise alone fills shell-guard (D75): the double-filler notice rigger-windows caused never comes back.
D75_LINE = ("rigger: slot shell-guard is filled by rigger-shellwise and rigger-windows; disable one with "
            "claude plugin disable <name>")
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

    def __init__(self, root=PLUGINS, os_name=None, **environ):
        """`os_name` pins the system the rules `requires: os:...` are judged on; None is this host's."""
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
            saved = assemble._this_os
            if os_name is not None:
                assemble._this_os = lambda: os_name
            try:
                self.main = assemble.assemble(inputs, "main")
                self.subagent = assemble.assemble(inputs, "subagent", "general-purpose")
            finally:
                assemble._this_os = saved
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
        self.assertEqual(len(dirs), 10)
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
        state = 'sh "${CLAUDE_PLUGIN_ROOT}/bin/py.sh" -S "${CLAUDE_PLUGIN_ROOT}/tools/state/state.py" hook || true'
        parts = [part_command(i) for i in range(1, SESSION_PARTS + 1)]
        for source, commands in (
            ("startup", [harness_start, state] + parts),
            ("compact", [compact_guard, state] + parts),
            ("clear", [state] + parts),
            ("resume", [state]),
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
        # Core's state file, Skill triggers table and Slots take part 1, so five main fills reach part 6; core's
        # table also takes a subagent's part 1 (D38), so three subagent fills reach part 4.
        dirs = [self.fill_module("mainfill", SESSION_PARTS - 1, "main"),
                self.fill_module("subfill", SUBAGENT_PARTS - 1, "subagent")]
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
        self.assertEqual(joined.count(b"x" * FILL), SUBAGENT_PARTS - 1)


class SessionStartTests(HookRun):
    def expected_headings(self, audience):
        found = []
        for root in module_dirs():
            for entry in load_manifest(root)["rules"]:
                if entry["audience"] in ("both", audience):
                    text = assembled().text(root.name, entry["file"][len("rules/"):], audience).decode("utf-8")
                    found.extend(heading_lines(text))
        self.assertGreaterEqual(len(found), PRINTED_MIN_FILES - 2)  # 17 main-session files on Windows; 00-scope.md is a preamble, no heading
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
                self.assertNotIn(D75_LINE, lines)
                self.assertEqual([line for line in lines if line.startswith("rigger: ")], [])
                for root in dirs:
                    if root.name == "rigger-platform":
                        # its rule requires os:windows: the root line comes with the rule, on Windows only
                        self.assertEqual(root_line(root) in joined, os.name == "nt")
                    # no rules, no root line
                    elif root.name not in ("rigger-panel", "rigger-review", "rigger-shellwise", "rigger-testing"):
                        self.assertIn(root_line(root), joined)

    def test_resume_prints_nothing(self):
        self.assertEqual(self.session(module_dirs(), "resume"), [b""] * SESSION_PARTS)


class SubagentStartTests(HookRun):
    def test_a_worker_gets_the_root_line_and_the_shell_line_on_windows_only(self):
        for agent in WORKERS:
            with self.subTest(agent=agent):
                text = b"".join(self.context(out) for out in self.subagent(module_dirs(), agent)).decode("utf-8")
                self.assertEqual(root_line(PLATFORM).decode("utf-8") + SHELL_HEADING + "\n" in text, os.name == "nt")
                self.assertEqual(SHELL_LINE in text, os.name == "nt")
                self.assertEqual(without_root_lines(text.encode("utf-8")),
                                 without_root_lines(assembled().whole("subagent")))

    def test_a_worker_gets_the_skill_table(self):
        text = assembled().whole("subagent").decode("utf-8")
        table = rules_text("rigger-core", "skills.md", "subagent").decode("utf-8")
        self.assertIn(TABLE_HEADER, table)
        self.assertIn(table, text)

    def test_skipped_agents_get_nothing(self):
        for agent in common.SKIPPED_AGENTS:
            with self.subTest(agent=agent):
                self.assertEqual(self.subagent(module_dirs(), agent), [b""] * SUBAGENT_PARTS)


class DropTests(unittest.TestCase):
    def test_a_basename_drops_only_its_own_file(self):
        found = Assembled(os_name="windows", HARNESS_RULES_DROP="windows.md")
        self.assertEqual(found.text("rigger-platform", "windows.md"), b"")
        self.assertIn(b"`rigger-platform:windows-dev-env`", found.text("rigger-core", "skills.md"))
        self.assertEqual(found.text("rigger-core", "skills.md"),
                         Assembled(os_name="windows").text("rigger-core", "skills.md"))

    def test_the_shell_section_goes_when_its_heading_is_dropped(self):
        # The rule has no preamble before its heading since the platform module took it over.
        text = Assembled(os_name="windows", HARNESS_RULES_DROP=SHELL_HEADING).whole("subagent").decode("utf-8")
        self.assertNotIn(SHELL_HEADING + "\n", text)
        self.assertNotIn(SHELL_LINE, text)


class SkillTableTests(unittest.TestCase):
    def table_rows(self, audience="main"):
        text = rules_text("rigger-core", "skills.md", audience).decode("utf-8")
        self.assertIn(TABLE_HEADER, text)
        return text.split(TABLE_HEADER)[1].splitlines()

    def test_the_one_table_lists_every_module_skill_once_by_installed_name(self):
        for audience in ("main", "subagent"):
            with self.subTest(audience=audience):
                matches = [ROW_RE.fullmatch(row) for row in self.table_rows(audience)]
                self.assertNotIn(None, matches)
                listed = sorted((match.group(1), match.group(2)) for match in matches)
                for plugin in ("rigger-core", "rigger-testing", "rigger-platform", "rigger-wp"):
                    skills = sorted(path.parent.name for path in (PLUGINS / plugin / "skills").glob("*/SKILL.md"))
                    self.assertGreater(len(skills), 0)
                    if plugin == "rigger-platform" and os.name != "nt":
                        skills = []  # its three rows require os:windows
                    # Without superpowers, the testing slot's row reads `rigger-testing:testing` alone (D39).
                    self.assertEqual(sorted(skill for owner, skill in listed if owner == plugin), skills, plugin)

    def test_exactly_one_rules_file_holds_a_table_placeholder(self):
        holding = []
        for root in module_dirs():
            for path in sorted((root / "rules").glob("*.md")):
                raw = path.read_text(encoding="utf-8")
                self.assertNotIn("| Before | Skill |", raw)
                holding += [path.relative_to(PLUGINS).as_posix()] * len(
                    re.findall(r"^\{\{triggers:[^{}\s]+\}\}$", raw, re.M))
        self.assertEqual(holding, ["rigger-core/rules/skills.md"])


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
            "Red runs: in a worktree, or in place only through `python \"<rigger-testing plugin folder>/tools/"
            "safe-edit/safe_edit.py\"` with `--run` (needs rigger-testing).",
            self.text("tests.md"))
        self.assertIn(
            "go through the Edit tool or `safe_edit.py --path FILE --find TEXT --replace TEXT --count N` (literal, counted; needs rigger-testing)",
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

    def test_the_hunk_list_names_the_kinds_the_wordpress_skill_maps(self):
        """rigger-wp-traps maps WordPress options, capabilities and nonces onto these words (D98)."""
        self.assertIn("settings, permissions or auth",
                      " ".join(rules_text("rigger-talk", "talk.md").decode("utf-8").split()))


class StateFileTests(unittest.TestCase):
    def test_the_bullet_is_under_its_own_heading_and_is_for_the_main_session(self):
        lines = rules_text("rigger-core", "state-file.md").decode("utf-8").split("\n")
        self.assertEqual(heading_lines("\n".join(lines)), ["## State file for long sessions"])
        self.assertEqual(lines[1], "- **Long sessions keep a state file; they never hand off.** Main session only (subagents never")
        self.assertEqual(lines[10:12], [
            "  without asking or writing a handoff.",
            "- **The state file stays under 9 000 characters,** all that comes back after compaction."])
        self.assertNotIn("coordinator", "\n".join(lines))
        self.assertEqual(lines[-2:], ["  on stale items named at session start.", ""])
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

    def test_the_brainstorming_route_names_the_table_not_a_slot(self):
        # D37: no rules sentence names an optional slot.
        self.assertIn("never waive brainstorming or planning (load the brainstorm and plans skills the Skill "
                      "triggers table names), the thinking pass", self.plans())
        raw = (PLUGINS / "rigger-rules" / "rules" / "plans-main.md").read_text(encoding="utf-8")
        self.assertNotIn("{{slot:", raw)

    def test_the_state_file_rule_names_the_plan_and_stop_keys(self):
        text = " ".join(rules_text("rigger-core", "state-file.md").decode("utf-8").split())
        self.assertIn("`updated: <ISO time>` / `plan:`, `stop:` when set / `---`", text)


class TestingSkillTriggerTests(unittest.TestCase):
    """Where the TDD skill is loaded, the testing skill that overrides it is loaded too."""

    def test_the_testing_skill_rides_every_tdd_trigger(self):
        # In the row data (manifests and the level-1 defaults), every row that loads the TDD slot loads the
        # testing slot before it (D47).
        # `triggers` is optional (manifest.py `_triggers`: absent reads as [])
        rows = [row for root in module_dirs() for row in load_manifest(root).get("triggers", [])]
        with open(CORE / "rigger-defaults.json", encoding="utf-8") as handle:
            rows += list(json.load(handle)["triggers"].values())
        tdd = [row["load"] for row in rows if "slot:process.tdd" in row["load"]]
        self.assertTrue(tdd, "no row loads slot:process.tdd")
        for load in tdd:
            self.assertIn("slot:testing", load[:load.index("slot:process.tdd")], load)
        # With superpowers enabled, the rendered cell is the one 0.4.0 printed.
        stub = Path(tempfile.mkdtemp(prefix="superpowers-stub-"))
        self.addCleanup(shutil.rmtree, str(stub), True)
        common.write_bytes(stub / ".claude-plugin" / "plugin.json", json.dumps({"name": "superpowers"}))
        dirs = os.pathsep.join(str(path) for path in module_dirs() + [stub])
        text = Assembled(RIGGER_MODULE_DIRS=dirs).text("rigger-core", "skills.md").decode("utf-8")
        cells = [line.split("|")[-2] for line in text.splitlines()
                 if line.startswith("|") and "superpowers:test-driven-development" in line]
        self.assertEqual(cells, [" `rigger-testing:testing`, then `superpowers:test-driven-development` "])
        worker = " ".join((PLUGINS / "rigger-core" / "agents" / "worker.md").read_text(encoding="utf-8").split())
        self.assertIn("Load the skills the Skill triggers table names for writing code or tests and for a bug or "
                      "failing test, each before that step.", worker)
        lines = TESTING_SKILL.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[:4], ["---", "name: testing", "description: >-",
                                     "  Load BEFORE writing, changing or reviewing tests or a red run: a feature or bugfix test, a test"])


def flat_file(path):
    return " ".join(path.read_text(encoding="utf-8").split())


class PortedLessonTests(unittest.TestCase):
    """General lessons ported from the source harness (D99), one pin each."""

    def test_the_lo_fi_trap_moved_to_the_ui_skill_and_its_trigger(self):
        skill = CORE / "skills" / "web-ui-traps" / "SKILL.md"
        description = " ".join(skill.read_text(encoding="utf-8").split("\n---", 1)[0].split())
        self.assertIn("lo-fi screens", description)
        self.assertIn("**Lo-fi means no styling, not no design.** Design each screen from its role's task and "
                      "decisions (what they must do, what they see, what comes next), with the UI that task needs: "
                      "statuses, filters, history, empty and error states.", flat_file(skill))
        self.assertNotIn("lo-fi", flat_file(PLUGINS / "rigger-rules" / "rules" / "plans-main.md").lower())
        rows = [row for row in load_manifest(CORE)["triggers"] if row["id"] == "rigger-core.web-ui-traps"]
        self.assertEqual(len(rows), 1, rows)
        self.assertIn("lo-fi screens", rows[0]["before"])

    def test_an_override_table_is_tested_where_it_differs_from_the_default(self):
        self.assertIn("A table that overrides a default rule (holidays, closures) is tested only with inputs whose "
                      "result differs from the default (a holiday on a weekday), so deleting the table turns it red.",
                      flat_file(TESTING_SKILL))

    def test_a_scratch_copy_of_a_worktree_drops_its_git_pointer(self):
        self.assertIn("A scratch copy of a worktree deletes its `.git` first: the copied pointer makes git act on "
                      "the real worktree.", flat_file(TESTING_SKILL))

    def test_a_reviewer_re_measures_a_sample_of_visual_measurements(self):
        self.assertIn("recount an agent's total, and have a read-only reviewer re-measure a sample of a builder's "
                      "own visual measurements, before relaying it.",
                      flat_file(CORE / "skills" / "orchestrating-subagents" / "SKILL.md"))


class RulesSizeTests(unittest.TestCase):
    def test_rules_text_stays_under_the_ceiling(self):
        root = Path(os.environ.get("RIGGER_RULES_ROOT") or PLUGINS)
        found = Assembled(root)
        self.assertNotIn(D75_LINE, found.main.notices + found.subagent.notices)
        self.assertEqual((found.main.notices, found.subagent.notices), ([], []), "rules assembled from %s" % root)
        main_ids = {section["id"] for section in found.main.sections}
        blocks = list(found.main.blocks) + [block for block, section in zip(found.subagent.blocks, found.subagent.sections)
                                            if section["id"] not in main_ids]
        files = {(s["module"], s["file"]) for s in found.main.sections + found.subagent.sections
                 if s["id"] != SLOTS_ID}
        self.assertGreaterEqual(len(files), PRINTED_MIN_FILES, "rules files read from %s" % root)
        # `## Slots` is generated and has a ceiling of its own (D50).
        slots = [block for block in blocks if block[2] == SLOTS_ID]
        self.assertEqual(len(slots), 1)
        self.assertLessEqual(len(slots[0][1]), SLOTS_CEILING_BYTES)
        self.assertLessEqual(sum(len(block[1]) for block in blocks if block[2] != SLOTS_ID), RULES_CEILING_BYTES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
